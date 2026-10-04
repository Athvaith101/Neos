"""
ledger.py -- Local-first verification ledger (hash chain, NOT a blockchain).

Records what was REQUESTED and what was MEASURED as delivered. Integrity comes
from a SHA-256 hash chain: each record stores the previous record's hash, so any
later edit, deletion or reordering breaks `verify()`. It needs no network and no
consensus, which is exactly why it is used instead of a distributed ledger.

Rules
  * Expired commands are REJECTED, never executed late (status rejected_expired).
  * Records written while offline carry offline=True and are synchronised when
    connectivity returns; the chain stays valid either way.
  * Delivery is measured against a named baseline; "delivered" is a measurement,
    not a promise, and nothing here implies payment or settlement.
"""
from __future__ import annotations
import csv, hashlib, io, json
from dataclasses import dataclass, field

GENESIS = "0" * 64
FIELDS = ["seq", "timestamp", "request_id", "direction", "requested_kw", "requested_duration_h",
          "delivered_kw", "measured_duration_h", "baseline_id", "config_id", "status",
          "offline", "note", "prev_hash", "hash"]


@dataclass
class Command:
    request_id: str
    direction: str                  # 'reduce_import' | 'increase_import' (charge) | 'discharge' | 'charge'
    kw: float
    duration_h: float
    issued_at: float                # hours since scenario start
    expires_at: float               # a command must START before this time
    baseline_id: str = "none"


def _digest(rec: dict) -> str:
    body = {k: rec[k] for k in FIELDS if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=float).encode()).hexdigest()


class VerificationLedger:
    def __init__(self, config_id="unspecified"):
        self.config_id = config_id
        self.records: list[dict] = []
        self.outbox: list[int] = []          # seq numbers not yet synchronised

    # -- command intake -------------------------------------------------------
    def receive(self, cmd: Command, now: float, online: bool = True):
        """Accept or reject a remote command. Returns (accepted, record)."""
        if now > cmd.expires_at:
            rec = self._append(now, cmd, 0.0, 0.0, "rejected_expired", online,
                               f"received {now - cmd.expires_at:.2f} h after expiry")
            return False, rec
        return True, None

    def decline(self, cmd: Command, now: float, available_kw: float, online: bool = True):
        """The envelope could not support the command: say so up front instead of accepting and failing."""
        return self._append(now, cmd, 0.0, 0.0, "declined_insufficient_flexibility", online,
                            f"envelope offered {available_kw:.1f} kW vs {cmd.kw:.1f} kW requested")

    def settle(self, cmd: Command, now: float, delivered_kw: float, measured_h: float,
               online: bool = True, note: str = ""):
        """Record the MEASURED outcome of an accepted command."""
        if cmd.kw <= 0:
            status = "no_request"
        elif delivered_kw >= 0.95 * cmd.kw and measured_h >= 0.95 * cmd.duration_h:
            status = "delivered"
        elif delivered_kw > 0.05 * cmd.kw:
            status = "partial"
        else:
            status = "not_delivered"
        return self._append(now, cmd, delivered_kw, measured_h, status, online, note)

    def _append(self, ts, cmd, delivered_kw, measured_h, status, online, note):
        rec = dict(seq=len(self.records), timestamp=float(ts), request_id=cmd.request_id,
                   direction=cmd.direction, requested_kw=float(cmd.kw),
                   requested_duration_h=float(cmd.duration_h), delivered_kw=float(delivered_kw),
                   measured_duration_h=float(measured_h), baseline_id=cmd.baseline_id,
                   config_id=self.config_id, status=status, offline=not online, note=note,
                   prev_hash=self.records[-1]["hash"] if self.records else GENESIS)
        rec["hash"] = _digest(rec)
        self.records.append(rec)
        if not online:
            self.outbox.append(rec["seq"])
        return rec

    # -- integrity ------------------------------------------------------------
    def verify(self):
        """Returns (ok, first_bad_seq). Detects edits, deletions and reordering."""
        prev = GENESIS
        for i, r in enumerate(self.records):
            if r["seq"] != i or r["prev_hash"] != prev or _digest(r) != r["hash"]:
                return False, i
            prev = r["hash"]
        return True, None

    def sync(self):
        """Mark offline records as synchronised (returns how many were pending)."""
        n = len(self.outbox)
        self.outbox.clear()
        return n

    def to_csv(self):
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=FIELDS)
        w.writeheader()
        for r in self.records:
            w.writerow(r)
        return buf.getvalue()

    def summary(self):
        by = {}
        for r in self.records:
            by[r["status"]] = by.get(r["status"], 0) + 1
        req = sum(r["requested_kw"] * r["requested_duration_h"] for r in self.records
                  if r["status"] not in ("rejected_expired", "declined_insufficient_flexibility"))
        dlv = sum(r["delivered_kw"] * r["measured_duration_h"] for r in self.records
                  if r["status"] not in ("rejected_expired", "declined_insufficient_flexibility"))
        ok, bad = self.verify()
        return dict(n=len(self.records), by_status=by, requested_kwh=req, delivered_kwh=dlv,
                    utilisation=(dlv / req if req > 0 else None), chain_ok=ok, first_bad=bad,
                    expired_rejected=by.get("rejected_expired", 0),
                    declined=by.get("declined_insufficient_flexibility", 0), pending_sync=len(self.outbox))
