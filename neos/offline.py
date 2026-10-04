"""
offline.py -- Offline-first behaviour and offline participation.

Communications loss is a first-class scenario. While the link is down:
  * local telemetry, state estimation and scheduling continue within safe limits;
  * remote commands that expire are rejected, never executed late;
  * the local reserve policy stays active;
  * decisions are logged locally and synchronised when the link returns.

Offline participation: NEOS can print a noticeboard schedule and send short SMS
instructions so residents without smartphones can take part.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class CommsLink:
    """Deterministic link model: down during [t0, t1) in hours."""
    windows: tuple = ()

    def up(self, t_h):
        return not any(a <= t_h < b for a, b in self.windows)


def noticeboard(schedule, community="the community", tz="IST"):
    """schedule: list of dict(start_h, end_h, what, tier). Returns printable text."""
    def hhmm(h):
        return f"{int(h) % 24:02d}:{int(round((h % 1) * 60)) % 60:02d}"
    lines = [f"ENERGY NOTICE FOR {community.upper()}", "=" * 44]
    for s in sorted(schedule, key=lambda r: (r['tier'], r['start_h'])):
        lines.append(f"[T{s['tier']}] {hhmm(s['start_h'])}-{hhmm(s['end_h'])} {tz}: {s['what']}")
    lines += ["", "If supply is short, lighting, phone charging and water come first.",
              "Medical needs: tell the community office. Questions: ask your energy volunteer."]
    return "\n".join(lines)


def sms(schedule_item, max_len=160):
    t = (f"NEOS: {schedule_item['what']} {int(schedule_item['start_h']) % 24:02d}:"
         f"{int(round((schedule_item['start_h'] % 1) * 60)) % 60:02d}-"
         f"{int(schedule_item['end_h']) % 24:02d}:{int(round((schedule_item['end_h'] % 1) * 60)) % 60:02d}."
         f" Reply HELP for info.")
    return t if len(t) <= max_len else t[:max_len - 1] + "\u2026"


def shortage_notice(level, soc, hours_left):
    msg = {0: "Normal supply.",
           1: "Supply tight: avoid pumps and mills until further notice.",
           2: "Battery low: lighting and phone charging only."}[level]
    return f"NEOS: {msg} Battery {soc * 100:.0f}%, about {hours_left:.1f} h at current load."[:160]
