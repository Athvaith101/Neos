"""
grid.py -- Physics layer of the digital twin.

Two interchangeable backends behind ONE interface (`solve(inj) -> state`):

  OpenDSSGrid     OpenDSS via OpenDSSDirect.py, one *independent context* per
                  model (no shared global circuit), guarded by a per-model lock.
  ReferenceGrid   A pure-NumPy unbalanced three-phase backward/forward-sweep
                  solver of the same radial network. It exists so that the
                  control/safety logic can be tested and CI'd without an
                  OpenDSS install, and so OpenDSS can be cross-checked
                  (see validate_backends.py). It is NOT a replacement for it.

Topology (both backends): 11 kV source -> Dyn11 transformer -> 433 V bus
-> radial LV feeders of 40 m segments. Single-phase households rotate across
phases, so phase imbalance is real.

Every solve returns a COMPLETE constraint status. Nothing is ever labelled
safe unless: the solver converged, every output is finite, every LV bus
voltage is in band (phase-resolved), the transformer is within its kVA
rating AND every line is within its ampacity. See `constraint_report`.
"""
from __future__ import annotations
import math
import threading
import numpy as np

VLIM_LO, VLIM_HI = 0.94, 1.06        # CEA / IS 12360 LV band: +/- 6 %
LINE_NORMAMPS = 350.0
V_LN = 250.0                         # 0.433 kV line-line / sqrt(3), rounded as OpenDSS does
TAP = 1.015
TAN_PF95 = 0.3287                    # tan(acos(0.95))


# --------------------------------------------------------------- reporting
def constraint_report(st, vlo=VLIM_LO, vhi=VLIM_HI, tx_limit_pct=100.0,
                      line_limit_pct=100.0, tol=1e-9):
    """Complete constraint status for ONE solved state.

    Returns dict(ok, solver_ok, undervoltage, overvoltage, tx_overload,
    line_overload, violations[list of str]). `ok` is True only if ALL hold.
    """
    solver_ok = bool(st.get('converged', False) and st.get('finite', False))
    rep = dict(solver_ok=solver_ok, undervoltage=False, overvoltage=False,
               tx_overload=False, line_overload=False, violations=[])
    if not solver_ok:
        rep['violations'].append('solver_failed')
        rep['ok'] = False
        return rep
    rep['undervoltage'] = st['vmin'] < vlo - tol
    rep['overvoltage'] = st['vmax'] > vhi + tol
    rep['tx_overload'] = st['tx_loading'] > tx_limit_pct + tol
    rep['line_overload'] = st['line_loading_max'] > line_limit_pct + tol
    for k, msg in (('undervoltage', 'undervoltage'), ('overvoltage', 'overvoltage'),
                   ('tx_overload', 'transformer_overload'),
                   ('line_overload', 'line_ampacity')):
        if rep[k]:
            rep['violations'].append(msg)
    rep['ok'] = not rep['violations']
    return rep


def _state_from(vmag_pu, tx_p, tx_q, tx_kva_rating, losses_kw, line_pct,
                converged, vlo=VLIM_LO, vhi=VLIM_HI):
    vmag_pu = np.asarray(vmag_pu, float)
    line_pct = np.asarray(line_pct, float)
    finite = bool(vmag_pu.size > 0 and np.all(np.isfinite(vmag_pu))
                  and np.all(np.isfinite(line_pct))
                  and np.isfinite(tx_p) and np.isfinite(tx_q) and np.isfinite(losses_kw))
    if not finite:
        return dict(converged=bool(converged), finite=False, vmin=float('nan'),
                    vmax=float('nan'), v_violations=0, tx_kw=float('nan'),
                    tx_kvar=float('nan'), tx_kva=float('nan'), tx_loading=float('nan'),
                    losses_kw=float('nan'), line_loading_max=float('nan'),
                    n_lines_over=0, n_lv_nodes=int(vmag_pu.size))
    s = math.hypot(tx_p, tx_q)
    return dict(
        converged=bool(converged), finite=True,
        vmin=float(vmag_pu.min()), vmax=float(vmag_pu.max()),
        v_violations=int(((vmag_pu < vlo) | (vmag_pu > vhi)).sum()),
        n_lv_nodes=int(vmag_pu.size),
        tx_kw=float(tx_p), tx_kvar=float(tx_q), tx_kva=float(s),
        tx_loading=100.0 * s / tx_kva_rating,
        losses_kw=float(losses_kw),
        line_loading_max=float(line_pct.max()) if line_pct.size else 0.0,
        n_lines_over=int((line_pct > 100.0).sum()),
    )


# ------------------------------------------------------------ common base
class _GridBase:
    backend = 'base'

    def __init__(self, nbhd, tx_kva=630.0):
        self.n = nbhd
        self.tx_kva = float(tx_kva)
        self.lock = threading.RLock()       # per-model, never global
        self.node_names = [nm for nm, _, _ in nbhd.nodes]
        self.n_solves = 0
        self.z_scale = 1.0

    def solve(self, inj):
        raise NotImplementedError

    def sensitivity(self, base_inj, probe_kw=25.0):
        """dVmin/dP, a LINEAR voltage proxy for the optimiser."""
        b = self.solve(base_inj)
        pert = dict(base_inj)
        keys = [(nm, ph) for nm in self.node_names for ph in (1, 2, 3)]
        share = probe_kw / len(keys)
        for k in keys:
            pert[k] = pert.get(k, 0.0) + share
        p = self.solve(pert)
        return (p['vmin'] - b['vmin']) / probe_kw, b


# -------------------------------------------------------------- reference
class ReferenceGrid(_GridBase):
    """Unbalanced 3-phase backward/forward sweep on radial feeders.

    Line model: 3x3 phase-impedance matrix built from sequence data
    (Zs=(Z0+2Z1)/3, Zm=(Z0-Z1)/3), neutral assumed solidly earthed (neutral
    voltage rise ignored). Loads are constant-PQ per (bus,phase).
    """
    backend = 'reference'

    def __init__(self, nbhd, tx_kva=630.0):
        super().__init__(nbhd, tx_kva)
        feeders = sorted({f for _, f, _ in nbhd.nodes})
        self.chains = []
        for f in feeders:
            chain = sorted([(d, nm) for nm, ff, d in nbhd.nodes if ff == f])
            self.chains.append([nm for _, nm in chain])
        self.F = len(self.chains)
        self.S = max(len(c) for c in self.chains)
        self.node_pos = {}
        for fi, ch in enumerate(self.chains):
            for si, nm in enumerate(ch):
                self.node_pos[nm] = (fi, si)
        z1 = complex(0.122, 0.071)
        z0 = complex(0.49, 0.28)
        zs, zm = (z0 + 2 * z1) / 3, (z0 - z1) / 3
        L = 0.040
        self.Zseg_base = L * np.array([[zs, zm, zm], [zm, zs, zm], [zm, zm, zs]])
        self.Zseg = self.Zseg_base.copy()
        self.z_scale = 1.0
        # source + transformer series impedance referred to the LV side
        zb = (0.433 ** 2) / (self.tx_kva / 1000.0)
        r_tx = (0.55 + 0.55) / 100.0 * zb
        x_tx = 0.05 * zb
        z_src = (0.433 / 11.0) ** 2 * (11.0 ** 2 / 120.0)
        self.z_up = complex(r_tx, x_tx + z_src)
        self.a = np.exp(-2j * np.pi / 3 * np.arange(3))        # 0, -120, +120 deg
        self.V0 = TAP * V_LN * self.a
        m = np.zeros((self.F, self.S, 3), bool)
        for f, ch in enumerate(self.chains):
            m[f, :len(ch), :] = True
        self._mask = m

    def set_impedance_scale(self, scale):
        """Scale the LV line impedance (the physical parameter that digital-twin
        calibration identifies). 1.0 = nameplate."""
        with self.lock:
            self.z_scale = float(scale)
            self.Zseg = self.Zseg_base * self.z_scale

    def solve(self, inj):
        with self.lock:
            self.n_solves += 1
            F, S = self.F, self.S
            P = np.zeros((F, S, 3))
            for (nm, ph), kw in inj.items():
                pos = self.node_pos.get(nm)
                if pos is not None and 1 <= ph <= 3:
                    P[pos[0], pos[1], ph - 1] += kw
            Q = np.where(P > 0, TAN_PF95 * P, 0.0)
            Sload = (P + 1j * Q) * 1000.0                      # VA per phase
            V = np.broadcast_to(self.V0, (F, S, 3)).astype(complex).copy()
            Vlv = self.V0.copy()
            converged = False
            Ib = np.zeros((F, S, 3), complex)
            with np.errstate(all='ignore'):
                for _ in range(100):
                    Vprev = V.copy()
                    # constant-PQ with the OpenDSS vminpu clamp (0.75 pu)
                    mag = np.abs(V)
                    vm = np.maximum(mag, 0.75 * V_LN)
                    Vc = np.where(mag > 0, V / np.where(mag > 0, mag, 1.0) * vm, vm)
                    Iload = np.conj(Sload / Vc)
                    Ib = np.flip(np.cumsum(np.flip(Iload, 1), 1), 1)   # downstream sums
                    Itot = Ib[:, 0, :].sum(0)
                    Vlv = self.V0 - self.z_up * Itot
                    Vk = np.broadcast_to(Vlv, (F, 3))
                    for s in range(S):
                        Vk = Vk - Ib[:, s, :] @ self.Zseg.T
                        V[:, s, :] = Vk
                    if not np.all(np.isfinite(V)):
                        break
                    if np.max(np.abs(V - Vprev)) < 1e-6 * V_LN:
                        converged = True
                        break
                Itot = Ib[:, 0, :].sum(0)
                S_prim = np.sum(self.V0 * np.conj(Itot))       # VA at the tx primary
                vpu = np.concatenate([(np.abs(V)[self._mask]) / V_LN,
                                      np.abs(Vlv) / V_LN])
                loss_lines = 0.0
                for f in range(F):
                    for s in range(len(self.chains[f])):
                        i = Ib[f, s]
                        loss_lines += float(np.real(np.vdot(i, self.Zseg @ i)))
                loss_tx = float(np.real(self.z_up) * np.sum(np.abs(Itot) ** 2))
                lp = (np.abs(Ib) / LINE_NORMAMPS * 100.0)[self._mask]
                st = _state_from(vpu, S_prim.real / 1000.0, S_prim.imag / 1000.0,
                                 self.tx_kva, (loss_lines + loss_tx) / 1000.0,
                                 lp, converged)
            st['backend'] = self.backend
            return st


# ---------------------------------------------------------------- OpenDSS
class OpenDSSGrid(_GridBase):
    """OpenDSS backend. Each instance owns an INDEPENDENT OpenDSS context, so
    building a second neighbourhood can no longer replace the circuit under an
    earlier model. Access is serialised per model with `self.lock`.

    NOTE: written against OpenDSSDirect.py >= 0.9 (`dss.NewContext()`); it is
    NOT exercised in the container this repo was refactored in (no OpenDSS
    available there). Run `python validate_backends.py` on a machine that has it.
    """
    backend = 'opendss'

    def __init__(self, nbhd, tx_kva=630.0):
        super().__init__(nbhd, tx_kva)
        import opendssdirect as dss          # import here: optional dependency
        if not hasattr(dss, 'NewContext'):
            raise RuntimeError(
                'This OpenDSSDirect.py has no NewContext(); refusing to fall back '
                'to the shared global circuit (that is the bug this class fixes). '
                'Upgrade to opendssdirect.py>=0.9 or use ReferenceGrid.')
        self.ctx = dss.NewContext()
        self._loadmap = {}
        self._build()

    def _cmd(self, s):
        self.ctx.Text.Command(s)

    def _build(self):
        c = self._cmd
        with self.lock:
            self.ctx.Basic.ClearAll()
            c("new circuit.NEOS basekv=11.0 pu=1.00 phases=3 bus1=SRC MVAsc3=120 MVAsc1=90")
            c("new transformer.DT phases=3 windings=2 xhl=5.0 %loadloss=1.1")
            c(f"~ wdg=1 bus=SRC conn=delta kv=11.0 kva={self.tx_kva} %r=0.55")
            c(f"~ wdg=2 bus=LVBUS.1.2.3.0 conn=wye kv=0.433 kva={self.tx_kva} %r=0.55")
            c(f"new linecode.lv95 nphases=3 r1=0.122 x1=0.071 r0=0.49 x0=0.28 units=km "
              f"normamps={LINE_NORMAMPS:.0f}")
            self._line_names = []
            for f in sorted({f for _, f, _ in self.n.nodes}):
                chain = sorted([(d, nm) for nm, ff, d in self.n.nodes if ff == f])
                prev = "LVBUS"
                for k, (d, bus) in enumerate(chain, 1):
                    nm = f"l_{f}_{k}"
                    c(f"new line.{nm} bus1={prev}.1.2.3 bus2={bus}.1.2.3 phases=3 "
                      f"linecode=lv95 length=0.040 units=km")
                    self._line_names.append(nm)
                    prev = bus
            for nm, f, s in self.n.nodes:
                for ph in (1, 2, 3):
                    lid = f"{nm}_p{ph}"
                    c(f"new load.{lid} bus1={nm}.{ph} phases=1 kv=0.25 kw=0.1 pf=0.95 "
                      f"model=1 vminpu=0.75 vmaxpu=1.25")
                    self._loadmap[(nm, ph)] = lid
            c(f"edit transformer.DT wdg=2 tap={TAP}")
            c("set voltagebases=[11.0, 0.433]")
            c("calcvoltagebases")
            c("set mode=snapshot")
            c("set maxcontroliter=30")
            # LV nodes are selected by ELECTRICAL IDENTITY (bus name), never by
            # per-unit magnitude, so a collapsed voltage is still measured.
            lv = {nm.lower() for nm in self.node_names} | {'lvbus'}
            self._lv_idx = [i for i, n in enumerate(self.ctx.Circuit.AllNodeNames())
                            if n.split('.')[0].lower() in lv and n.split('.')[-1] in '123']

    def set_impedance_scale(self, scale):
        with self.lock:
            self.z_scale = float(scale)
            self._cmd(f"edit linecode.lv95 r1={0.122 * scale:.6f} x1={0.071 * scale:.6f} "
                      f"r0={0.49 * scale:.6f} x0={0.28 * scale:.6f}")

    def solve(self, inj):
        with self.lock:
            self.n_solves += 1
            for (nm, ph), lid in self._loadmap.items():
                kw = float(inj.get((nm, ph), 0.0))
                kvar = TAN_PF95 * kw if kw > 0 else 0.0
                self._cmd(f"edit load.{lid} kw={kw:.4f} kvar={kvar:.4f}")
            self.ctx.Solution.Solve()
            converged = bool(self.ctx.Solution.Converged())
            vm = np.array(self.ctx.Circuit.AllBusMagPu())
            vm = vm[self._lv_idx] if len(vm) else vm
            self.ctx.Circuit.SetActiveElement("transformer.DT")
            p = np.array(self.ctx.CktElement.Powers())
            tx_p = float(p[0] + p[2] + p[4])
            tx_q = float(p[1] + p[3] + p[5])
            loss = self.ctx.Circuit.Losses()
            lp = []
            for nm in self._line_names:
                self.ctx.Circuit.SetActiveElement(f"line.{nm}")
                cur = np.array(self.ctx.CktElement.CurrentsMagAng())[0:6:2]
                lp.append(cur.max() / LINE_NORMAMPS * 100.0)
            st = _state_from(vm, tx_p, tx_q, self.tx_kva, float(loss[0]) / 1000.0,
                             lp, converged)
            st['backend'] = self.backend
            return st


# --------------------------------------------------------------- factory
def have_opendss():
    try:
        import opendssdirect  # noqa: F401
        return True
    except Exception:
        return False


def make_grid(nbhd, tx_kva=630.0, backend='auto'):
    """backend: 'auto' (OpenDSS if installed, else reference), 'opendss', 'reference'."""
    if backend == 'reference':
        return ReferenceGrid(nbhd, tx_kva)
    if backend == 'opendss':
        return OpenDSSGrid(nbhd, tx_kva)
    if have_opendss():
        try:
            return OpenDSSGrid(nbhd, tx_kva)
        except RuntimeError:
            pass
    return ReferenceGrid(nbhd, tx_kva)
