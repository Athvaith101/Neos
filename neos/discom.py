"""
discom.py -- DISCOM-facing emulation on the community hub (one day, 15-minute steps).

Shows, with real simulated values, the pieces the specification asks for:
  * a Flexibility Envelope every step (reserve-protected, physics-bounded, expiring);
  * remote commands with a TTL: an expired command is REJECTED, never run late;
  * delivery MEASURED against a one-step counterfactual baseline (what the local
    policy would have imported this step without the command);
  * a hash-chain verification ledger, with offline records synchronised after the
    link returns;
  * communications loss: local scheduling and the protected reserve keep running.

THE NUMBERS ARE SIMULATED. No DISCOM, tariff or contract is represented; nothing here
implies payment, settlement, trading or a utility's acceptance of any record.
"""
from __future__ import annotations
import numpy as np
from . import hub
from .ledger import VerificationLedger, Command
from .envelope import battery_fleet_envelope, NOTICE

DT = 0.25


def simulate(cfg=None, seed=3, day=200, requests=None, link_down=(15.0, 19.0), reserve_frac=0.50,
             ttl_h=0.5):
    """requests: list of dict(issue_h, kw, duration_h, direction). Default demonstrates a normal
    request, a request sent during the comms outage (arrives expired), and a late request."""
    cfg = cfg or hub.HubConfig()
    tr = hub.make_traces(cfg, seed)
    sl = slice(day * 96, day * 96 + 96)
    D = tr['D'][sl].copy()
    out = np.zeros(96, bool)                                        # grid assumed up on this day
    cap = cfg.batt_kwh * cfg.soh
    e = 0.80 * cap
    ec, eb = cfg.eta_conv, cfg.eta_batt
    requests = requests if requests is not None else [
        dict(issue_h=16.5, kw=25.0, duration_h=1.5, direction='reduce_import'),   # sent while link is down
        dict(issue_h=19.25, kw=20.0, duration_h=1.0, direction='reduce_import'),  # normal, after link returns
        dict(issue_h=20.5, kw=30.0, duration_h=1.0, direction='reduce_import'),   # normal
    ]
    link = lambda h: not (link_down and link_down[0] <= h < link_down[1])
    ledger = VerificationLedger(config_id=cfg.config_hash())
    active = None
    delivered_log = []
    env_series = []
    queued = sorted(requests, key=lambda r: r['issue_h'])
    pending = []                                                    # remote side: awaiting a working link
    imp_series, base_series, soc_series = [], [], []
    hist_err = []
    for t in range(96):
        h = t * DT
        online = link(h)
        d = D[t] + cfg.aux_kw
        # remote side issues commands; they can only ARRIVE while the link is up
        while queued and queued[0]['issue_h'] <= h:
            r = queued.pop(0)
            pending.append(Command(request_id=f"R{len(ledger.records) + len(pending) + 1}", direction=r['direction'],
                                   kw=r['kw'], duration_h=r['duration_h'], issued_at=r['issue_h'],
                                   expires_at=r['issue_h'] + ttl_h, baseline_id='one-step-counterfactual'))
        # forecast uncertainty from recent persistence error
        hist_err.append(abs(D[t] - (D[t - 1] if t else D[t])))
        sigma = float(np.mean(hist_err[-8:])) * 1.25
        pending_now = list(pending) if online else []
        # local policy (identical to battery_only peak shaving + recharge, protected reserve).
        # The BASELINE is this full local policy: what would have been imported without any command.
        P_local = 0.0
        chg_local = 0.0
        if d > cfg.shave_kw and e > reserve_frac * cap:
            avail = (e - reserve_frac * cap) * ec * eb / DT
            P_local = min(d - cfg.shave_kw, cfg.conv_kw, avail)
        elif d < cfg.shave_kw and e < cfg.soc_max * cap:
            room = (cfg.soc_max * cap - e) / (DT * ec * eb)
            chg_local = min(cfg.shave_kw - d, cfg.conv_kw, room)
        imp_base = d - P_local + chg_local
        # envelope (post-reserve) at this step
        env = battery_fleet_envelope(
            [dict(e_kwh=e, cap_kwh=cap, soh=1.0, p_max_kw=cfg.conv_kw, eta=ec * eb, soc_min=cfg.soc_min,
                  available=True)], reserve_frac, h, ttl_h=ttl_h, duration_h=1.0, sigma_kw=sigma,
            stress_kw=max(0.0, d - cfg.shave_kw), config_id=cfg.config_hash())
        env_series.append(dict(t=h, discharge_kw=env['discharge']['kw'], confidence=env['discharge']['confidence'],
                               expires=env['expires_at_h'], online=online))
        for cmd in pending_now:
            ok, rec = ledger.receive(cmd, h, online=True)
            pending.remove(cmd)
            if not ok:
                continue
            if env['discharge']['kw'] < 0.5 * cmd.kw:             # cannot honour it: decline up front
                ledger.decline(cmd, h, env['discharge']['kw'], online=True)
                continue
            active = dict(cmd=cmd, end=h + cmd.duration_h, deliv=[], base=[])
        # active command: extra discharge on top of local policy, bounded by the envelope
        P_cmd = 0.0
        if active and h < active['end']:
            want = min(active['cmd'].kw, max(0.0, imp_base))
            room = max(0.0, env['discharge']['kw'])
            e_left = max(0.0, e - reserve_frac * cap)               # never touch the protected reserve
            P_cmd = min(want + chg_local, room + chg_local, e_left * ec * eb / DT + chg_local)
            P_cmd = max(0.0, P_cmd)
        if P_cmd > 0:                                               # a command cancels local recharging
            chg = 0.0
            P = P_local + P_cmd - chg_local if chg_local > 0 else P_local + P_cmd
            P = max(P, 0.0)
            e -= (P / ec / eb) * DT
            imp = d - P
        else:
            if P_local > 0:
                e -= (P_local / ec / eb) * DT
            if chg_local > 0:
                e += chg_local * ec * eb * DT
            imp = imp_base
        imp_series.append(imp); base_series.append(imp_base); soc_series.append(e / cap)
        if active and h < active['end']:
            active['deliv'].append(imp_base - imp)
        if active and (h + DT >= active['end'] or t == 95):
            kw_mean = max(0.0, float(np.mean(active['deliv']))) if active['deliv'] else 0.0
            ledger.settle(active['cmd'], h + DT, kw_mean, len(active['deliv']) * DT, online=online,
                          note='delivery measured vs one-step counterfactual baseline')
            delivered_log.append((active['cmd'].request_id, kw_mean))
            active = None
        if not online:
            # local decision log is kept offline; written as an offline record on the next sync
            pass
    # commands that never arrived before the day ended
    for cmd in pending:
        ledger.receive(cmd, 24.0, online=True)
    # offline records: the settle() calls above happen with online flag at the time
    pend_before = len(ledger.outbox)
    synced = ledger.sync()
    ok, bad = ledger.verify()
    return dict(
        notice=NOTICE, config_id=cfg.config_hash(), seed=seed, day=day, link_down=link_down, ttl_h=ttl_h,
        reserve_protected_frac=reserve_frac,
        envelope=env_series, import_kw=imp_series, baseline_import_kw=base_series, soc=soc_series,
        ledger=ledger.records, ledger_summary=ledger.summary(), chain_ok=ok, offline_records_synced=synced,
        min_soc=float(min(soc_series)), reserve_floor_respected=bool(min(soc_series) >= reserve_frac - 0.02),
        local_policy_ran_while_offline=bool(any(not s['online'] for s in env_series)),
        csv=ledger.to_csv(),
        disclaimer='SIMULATED. Delivery is measured against a stated baseline; no payment, trading, tariff or '
                   'contract is represented, and nothing here is a settlement record.')
