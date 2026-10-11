"""
hub.py -- Community hub: household participation with OFFLINE-FIRST pledging.

SIMULATED PROTOCOL, not an app. There is no phone client, SMS gateway, USSD session or network in
this repo; "devices" are in-memory objects and "connectivity" is a flag. What IS implemented and tested:

  * a household pledges to cut/shift load (kW, step window) from a device that may be offline;
  * offline events wait in a local queue, each with a per-household sequence number (idempotency key);
  * on sync the hub applies them in sequence order, drops exact duplicates, and gives EVERY event an
    explicit outcome (applied / duplicate / expired / rejected) -- nothing is silently lost;
  * a later pledge for the same household+window supersedes an earlier one (last sequence wins);
  * an SMS-style text ("PLEDGE 1.5 72 80") is parsed to the same events, with errors returned to the sender;
  * only CONFIRMED (synced) pledges count toward committed capacity, and committed capacity is
    discounted by that household's measured past reliability before it is offered to a DISCOM request;
  * credits are PARTICIPATION POINTS in kWh of measured delivery (never money; no settlement is modelled).
"""
from __future__ import annotations
from dataclasses import dataclass, field
import re
import threading

APPLIED, DUPLICATE, EXPIRED, REJECTED = 'applied', 'duplicate', 'expired', 'rejected'
MAX_PLEDGE_KW = 10.0
PRIOR_RELIABILITY = 0.5          # neutral prior for a household with no history (disclosed assumption)


@dataclass
class Event:
    hh: str
    seq: int
    kind: str                     # 'pledge' | 'withdraw'
    start: int = 0
    end: int = 0
    kw: float = 0.0
    channel: str = 'app'
    outcome: str = 'pending'
    reason: str = ''


@dataclass
class Household:
    hh: str
    applied_seqs: set = field(default_factory=set)
    pledge: dict | None = None    # single active pledge: dict(start,end,kw,seq)
    delivered_kwh: float = 0.0
    promised_kwh: float = 0.0
    points: float = 0.0


class Device:
    """A household's phone/feature-phone. Holds its own sequence counter and offline queue."""
    def __init__(self, hh):
        self.hh, self.online, self.seq, self.queue = hh, True, 0, []

    def submit(self, hub, kind, start=0, end=0, kw=0.0, channel='app'):
        self.seq += 1
        ev = Event(self.hh, self.seq, kind, int(start), int(end), float(kw), channel)
        if self.online:
            hub.receive([ev], now=hub.now)
        else:
            ev.outcome = 'queued'
            self.queue.append(ev)
        return ev

    def sync(self, hub):
        if not self.online:
            return []
        evs, self.queue = self.queue, []
        hub.receive(evs, now=hub.now)
        return evs


class Hub:
    def __init__(self, households, now=0):
        self.h = {x: Household(x) for x in households}
        self.devices = {x: Device(x) for x in households}
        self.now = int(now)       # current step index
        self.log: list[Event] = []
        self._lock = threading.RLock()

    # ---- ingest
    def receive(self, events, now=None):
        with self._lock:
            for ev in sorted(events, key=lambda e: (e.hh, e.seq)):
                self.log.append(ev)
                hh = self.h.get(ev.hh)
                if hh is None:
                    ev.outcome, ev.reason = REJECTED, 'unknown household'; continue
                if ev.seq in hh.applied_seqs:
                    ev.outcome, ev.reason = DUPLICATE, 'sequence already applied'; continue
                if ev.kind == 'pledge':
                    if not (0 < ev.kw <= MAX_PLEDGE_KW) or ev.end <= ev.start or ev.start < 0:
                        ev.outcome, ev.reason = REJECTED, f'need 0<kw<={MAX_PLEDGE_KW} and end>start'; continue
                    if ev.end <= self.now:
                        hh.applied_seqs.add(ev.seq)
                        ev.outcome, ev.reason = EXPIRED, 'window already over when it reached the hub'; continue
                    cur = hh.pledge
                    if cur and cur['seq'] > ev.seq:
                        hh.applied_seqs.add(ev.seq)
                        ev.outcome, ev.reason = DUPLICATE, 'superseded by a later pledge already applied'; continue
                    hh.pledge = dict(start=ev.start, end=ev.end, kw=ev.kw, seq=ev.seq)
                elif ev.kind == 'withdraw':
                    if hh.pledge and hh.pledge['seq'] < ev.seq:
                        hh.pledge = None
                else:
                    ev.outcome, ev.reason = REJECTED, 'unknown event kind'; continue
                hh.applied_seqs.add(ev.seq)
                ev.outcome = APPLIED

    def sms(self, sender, text):
        """'PLEDGE <kw> <start> <end>' or 'WITHDRAW'. Returns (reply, event|None). Needs no data connection
        on the hub side beyond the gateway; the household's phone only needs voice/SMS coverage."""
        t = text.strip().upper()
        dev = self.devices.get(sender)
        if dev is None:
            return 'ERR unknown number', None
        m = re.fullmatch(r'PLEDGE\s+([0-9]+(?:\.[0-9]+)?)\s+(\d+)\s+(\d+)', t)
        if m:
            ev = Device.submit(_Online(dev), self, 'pledge', int(m.group(2)), int(m.group(3)), float(m.group(1)), 'sms')
            return f'{ev.outcome.upper()} {ev.reason}'.strip(), ev
        if t == 'WITHDRAW':
            ev = Device.submit(_Online(dev), self, 'withdraw', channel='sms')
            return f'{ev.outcome.upper()}', ev
        return 'ERR format: PLEDGE <kw> <start> <end> | WITHDRAW', None

    # ---- capacity and credit
    def reliability(self, hh):
        x = self.h[hh]
        if x.promised_kwh <= 0:
            return PRIOR_RELIABILITY
        return max(0.0, min(1.0, x.delivered_kwh / x.promised_kwh))

    def committed_kw(self, start, end):
        """Confirmed pledges covering the WHOLE window [start,end), raw and reliability-discounted."""
        raw = disc = 0.0
        n = 0
        for k, x in self.h.items():
            p = x.pledge
            if p and p['start'] <= start and p['end'] >= end:
                raw += p['kw']; disc += p['kw'] * self.reliability(k); n += 1
        return dict(raw_kw=raw, expected_kw=disc, households=n,
                    note='expected_kw is discounted by each household\'s measured past delivery '
                         f'(neutral prior {PRIOR_RELIABILITY} until history exists); it is an estimate, not a guarantee')

    def settle(self, start, end, achieved_kw, dt_h=0.25):
        """achieved_kw: {hh: measured reduction in kW over the window}. Updates reliability and points."""
        out = {}
        with self._lock:
            for k, x in self.h.items():
                p = x.pledge
                if not (p and p['start'] <= start and p['end'] >= end):
                    continue
                dur = (end - start) * dt_h
                prom = p['kw'] * dur
                got = min(prom, max(0.0, float(achieved_kw.get(k, 0.0))) * dur)
                x.promised_kwh += prom; x.delivered_kwh += got; x.points += got
                out[k] = dict(promised_kwh=prom, delivered_kwh=got)
        return out

    def snapshot(self):
        with self._lock:
            return dict(
                now=self.now,
                households=[dict(hh=k, pledge=x.pledge, reliability=self.reliability(k), points_kwh=round(x.points, 3),
                                 online=self.devices[k].online, queued=len(self.devices[k].queue))
                            for k, x in self.h.items()],
                log=[dict(hh=e.hh, seq=e.seq, kind=e.kind, start=e.start, end=e.end, kw=e.kw,
                          channel=e.channel, outcome=e.outcome, reason=e.reason) for e in self.log[-60:]],
                queued=[dict(hh=e.hh, seq=e.seq, kind=e.kind, start=e.start, end=e.end, kw=e.kw)
                        for d in self.devices.values() for e in d.queue],
                disclaimer='Simulated protocol: no phone client, SMS gateway or network exists in this repo. '
                           'Points are participation credit, not money; no settlement is modelled.')


class _Online:
    """Adapter so an SMS (which reaches the hub directly) bypasses the household's offline queue."""
    def __init__(self, dev):
        self._d = dev
        self.hh = dev.hh
        self.online = True

    @property
    def seq(self): return self._d.seq
    @seq.setter
    def seq(self, v): self._d.seq = v

