"""
Logical Me — reminders (server.py add-on), v3: two-sided, every number measured.

Handles PREMARKET / CHECK10 / HOD_PUT / LOD_CALL / TREND_MODE / RECAP payloads.
Zero API calls. Each line is either
  (a) a FACT computed from the payload, or
  (b) a HISTORY line quoting a number measured on your own data, with its n.
Where nothing was measured, the line says so instead of guessing.

Data behind the numbers (scripts: audit.py, rides.py, hod999.py, room.py, pm3.py):
  RTH 2m SPY, 107 days, Apr 20 -> Sep 21 2026 (main set)
  ETH 2m SPY, 44 days, Apr 27 -> Jun 29 2026 (real 999 + premarket)
One strong-uptrend regime, small samples: "what happened", not "what will happen".
SE = standard error of the mean P&L ($/share), one trade per day per cell where noted.

Wire-up in server.py:
    from recap_reminders import build_reminders, format_reminders
    elif sig in ("PREMARKET", "CHECK10", "HOD_PUT", "LOD_CALL", "TREND_MODE", "RECAP"):
        send_telegram(format_reminders(payload, build_reminders(payload)))
"""

SAMPLE = "107 days, SPY 2m, Apr-Sep 2026"
ETH_SAMPLE = "44 days with extended hours, Apr 27-Jun 29 2026"

# |gap| upper bound %, days, % filled by 10:30, by noon, by close
GAP_FILL = [
    (0.15, 27, 67, 74, 78),
    (0.30, 23, 39, 48, 57),
    (0.50, 24, 21, 25, 33),
    (1.00, 26, 12, 19, 23),
    (99.0,  6,  0,  0,  0),
]

# open-drive days: (days, % kept going same way 10:00->close, % re-entered yesterday's range after 10:00)
OPEN_DRIVE = {"UP": (15, 60, 40), "DOWN": (13, 62, 23)}

# Long at 10:00 on an up-drive, held to noon: +$0.80/share (15 days) vs +$0.08 on all days.
# Touch of the 8 then 20-trail: +$1.01 (SE 0.48, 12 days).  Down-drive: nothing measured to noon (13 days).
UP_DRIVE_RIDE = {"days": 15, "to_noon": 0.80, "base": 0.08, "pb_days": 12, "pb": 1.01, "pb_se": 0.48}
DOWN_DRIVE_DAYS = 13

# 200 vs 999 at 10:00 -> (days, % directional, % finished above their 10:00 price)
REGIME = {
    "BULL":      (32, 59, 53),
    "BULL_FLAT": (26, 38, 42),
    "BEAR":      (19, 37, 58),
    "BEAR_FLAT": (24, 38, 50),
}

# Simulated v4 signals by number of same-side failures already that day: (attempts, win%, avg $/share)
BY_FAILS = {
    "HOD": {0: (109, 33, -0.06), 1: (64, 28, 0.12), 2: (85, 20, 0.03)},
    "LOD": {0: (86, 38, -0.17), 1: (40, 45, 0.11), 2: (18, 38, 0.11)},
}

# HOD puts by day type (type is only known at the close = hindsight):
# (signals, days, win%, avg $/share, SE).  Target $2 / stop = high+0.10.  Efficiency = |close-open|/range.
HOD_BY_DAYTYPE = {
    "TREND DOWN":  (34, 22, 71, +1.02, 0.26),
    "NON-TRENDING": (96, 38, 41, +0.24, 0.15),
    "MIXED":       (58, 18, 29, -0.10, 0.17),
    "TREND UP":    (116, 27, 15, -0.35, 0.08),
}
DAYTYPE_DAYS = {"NON-TRENDING": 40, "TREND UP": 27, "TREND DOWN": 22, "MIXED": 18}

# HOD put exiting at the REAL 999, by how far the 999 sits below entry (ETH set):
# (lo $, hi $, signals, win%, avg $/share, SE).  Fixed $2 target on same signals: +0.02 / -0.22 / +0.39.
HOD_999_DIST = [
    (1, 3, 32, 28, +0.01, 0.22),
    (3, 5, 31, 16, -0.47, 0.18),
    (5, 99, 23, 39, +0.60, 0.37),
]

# Premarket (ETH set): price came back to the premarket extreme during RTH 70% of days vs 65% for
# yesterday's levels; with 3+ premarket pushes at that extreme: 16 of 20 days. Directional, weak, small n.
PM = {"days": 44, "touch_pct": 70, "base_pct": 65, "many_n": 20, "many_hit": 16}


def _f(d, k, default=None):
    try:
        return float(d.get(k))
    except (TypeError, ValueError):
        return default


def _b(d, k):
    return str(d.get(k, "")).lower() == "true"


def _i(d, k):
    try:
        return int(d.get(k, 0))
    except (TypeError, ValueError):
        return 0


def _gap_row(gap_pct):
    for row in GAP_FILL:
        if gap_pct <= row[0]:
            return row
    return GAP_FILL[-1]


def _dist_row(dist):
    for row in HOD_999_DIST:
        if row[0] <= dist < row[1]:
            return row
    return None


def build_reminders(p: dict) -> list[str]:
    sig = p.get("signal", "")
    L = []

    # ---------- PREMARKET (09:28) ----------
    if sig == "PREMARKET":
        pmh, pml, pdh, pdl = _f(p, "pm_high"), _f(p, "pm_low"), _f(p, "pd_high"), _f(p, "pd_low")
        pu_h, pu_l = _i(p, "pm_push_high"), _i(p, "pm_push_low")
        if pmh is not None:
            L.append(f"Premarket high {pmh:.2f}: rejected {pu_h} times. Premarket low {pml:.2f}: bounced {pu_l} times.")
        if pdh is not None and pdl is not None:
            L.append(f"Yesterday's range {pdl:.2f}-{pdh:.2f}.")
        L.append(f"History ({ETH_SAMPLE}): price came back to the premarket extreme in RTH {PM['touch_pct']}% of days, "
                 f"vs {PM['base_pct']}% for yesterday's levels. With 3+ pushes at the extreme: "
                 f"{PM['many_hit']} of {PM['many_n']} days. Directional only, small sample, either side.")
        return L

    # ---------- per-signal reminders ----------
    if sig in ("HOD_PUT", "LOD_CALL"):
        side = "HOD" if sig == "HOD_PUT" else "LOD"
        att = _i(p, "att_hod") if side == "HOD" else _i(p, "att_lod")
        failed = _i(p, "failed_hod") if side == "HOD" else _i(p, "failed_lod")
        n, w, avg = BY_FAILS[side][min(failed, 2)]
        L.append(f"{side} attempt #{att} today, {failed} failed so far. "
                 f"History ({SAMPLE}): after {min(failed, 2)}{'+' if failed >= 2 else ''} failed {side}s, "
                 f"{n} signals won {w}%, avg {avg:+.2f}/share.")
        close, ma999 = _f(p, "close"), _f(p, "ma999")
        if close is not None and ma999 is not None:
            if sig == "HOD_PUT" and ma999 < close:
                d = close - ma999
                row = _dist_row(d)
                if row:
                    L.append(f"999 ({ma999:.2f}) is ${d:.2f} below entry. Exiting at the 999 from that distance "
                             f"({ETH_SAMPLE}): {row[2]} signals, {row[3]}% won, avg {row[4]:+.2f} (SE {row[5]:.2f}). "
                             f"Not monotonic: $3-5 away was the worst bucket.")
                else:
                    L.append(f"999 ({ma999:.2f}) is ${d:.2f} below entry. Under $1: not measured.")
            elif sig == "HOD_PUT":
                L.append(f"999 ({ma999:.2f}) is above entry: not measured.")
            else:
                L.append(f"999 ({ma999:.2f}) is {abs(close - ma999):.2f} {'above' if ma999 > close else 'below'} entry. "
                         f"No LOD-call-to-999 test was run.")
        return L

    # ---------- shared fact block (CHECK10 / TREND_MODE / RECAP) ----------
    regime = p.get("regime", "")
    open_drive, drive_dir = _b(p, "open_drive"), p.get("drive_dir", "")
    failed_h, failed_l = _i(p, "failed_hod"), _i(p, "failed_lod")
    att_h, att_l = _i(p, "att_hod"), _i(p, "att_lod")
    trend_mode, trend_at, trend_n = p.get("trend_mode", ""), p.get("trend_at", ""), _i(p, "trend_n") or 2
    o, pdh, pdl, pdc = _f(p, "open"), _f(p, "pd_high"), _f(p, "pd_low"), _f(p, "pd_close")
    ma200, ma999 = _f(p, "ma200"), _f(p, "ma999")
    s_hi, s_lo, cl = _f(p, "sess_high"), _f(p, "sess_low"), _f(p, "close")

    # 1. Open-drive: both directions
    if open_drive and drive_dir in ("UP", "DOWN") and o is not None:
        d, cont, reent = OPEN_DRIVE[drive_dir]
        edge = f"above yesterday's high {pdh:.2f}" if drive_dir == "UP" else f"below yesterday's low {pdl:.2f}"
        L.append(f"Open-drive {drive_dir}: opened {o:.2f}, {edge}, not back inside. {d} such days ({SAMPLE}): "
                 f"{cont}% kept going that way 10:00->close, {reent}% re-entered yesterday's range.")
        if sig == "CHECK10":
            if drive_dir == "UP":
                r = UP_DRIVE_RIDE
                L.append(f"Riding it: long at 10:00 held to noon averaged {r['to_noon']:+.2f}/share ({r['days']} days) "
                         f"vs {r['base']:+.2f} on all days. Entry on a touch of the 8 with the 20 as trail: "
                         f"{r['pb']:+.2f} (SE {r['pb_se']:.2f}, {r['pb_days']} days).")
            else:
                L.append(f"Down-drive ({DOWN_DRIVE_DAYS} days): no edge measured from 10:00 to noon in either direction. "
                         f"The up-drive ride result does NOT mirror.")
    elif sig == "CHECK10" and o is not None:
        if pdl is not None and pdh is not None:
            L.append(f"No open-drive: opened {o:.2f} against yesterday's range {pdl:.2f}-{pdh:.2f}.")
        else:
            L.append(f"No open-drive: opened {o:.2f} (yesterday's range not in payload).")

    # 2. Gap fill (both directions)
    if o and pdc:
        gap = o - pdc
        gap_pct = abs(gap) / pdc * 100
        if gap_pct >= 0.05:
            row = _gap_row(gap_pct)
            filled = (s_lo is not None and s_lo <= pdc) if gap > 0 else (s_hi is not None and s_hi >= pdc)
            L.append(f"Gap {'up' if gap > 0 else 'down'} {gap_pct:.2f}% from {pdc:.2f}: {'filled' if filled else 'not filled'} so far. "
                     f"Gaps this size ({row[1]} days): filled by 10:30 {row[2]}%, noon {row[3]}%, close {row[4]}%.")

    # 3. Regime (fact + history, no direction claim)
    if regime in REGIME and ma200 is not None and ma999 is not None:
        d, directional, up = REGIME[regime]
        rel = "over" if ma200 > ma999 else "under"
        names = {"BULL": "both rising", "BEAR": "both falling", "BULL_FLAT": "not both rising", "BEAR_FLAT": "not both falling"}
        L.append(f"200 ({ma200:.2f}) {rel} 999 ({ma999:.2f}), {names[regime]}. {d} days at 10:00 ({SAMPLE}): "
                 f"{directional}% finished directional, {up}% finished above their 10:00 price.")

    # 4. Trend mode / failures
    if trend_mode in ("UP", "DOWN"):
        side = "HOD" if trend_mode == "UP" else "LOD"
        failed = failed_h if side == "HOD" else failed_l
        att = att_h if side == "HOD" else att_l
        L.append(f"Trend mode {trend_mode} tripped {trend_at}: {trend_n} failed {side}s. Today: {att} {side} attempts, {failed} failed.")
    if sig in ("RECAP", "TREND_MODE"):
        for side, failed in (("HOD", failed_h), ("LOD", failed_l)):
            if failed >= 1:
                t = BY_FAILS[side]
                L.append(f"Simulated {side}s: win rate {t[0][1]}% first try vs {t[2][1]}% after 2+ failures, "
                         f"avg {t[0][2]:+.2f} vs {t[2][2]:+.2f}/share. The hit rate fell; the average did not.")
                break

    # 5. RECAP: day type (hindsight) + the 999 fact
    if sig == "RECAP":
        if s_hi is not None and s_lo is not None and o is not None and cl is not None and s_hi > s_lo:
            eff = abs(cl - o) / (s_hi - s_lo)
            up = cl >= o
            dt = "TREND UP" if (eff >= 0.5 and up) else "TREND DOWN" if eff >= 0.5 else "NON-TRENDING" if eff < 0.3 else "MIXED"
            n, days, w, avg, se = HOD_BY_DAYTYPE[dt]
            L.append(f"Day type by efficiency {eff:.2f}: {dt} ({DAYTYPE_DAYS[dt]} of 107 days). "
                     f"HOD puts on those days: {n} signals / {days} days, {w}% won, avg {avg:+.2f} (SE {se:.2f}). "
                     f"Hindsight label: known only at the close, so it is not a 10:00 filter.")
        tag = p.get("tag999_at", "")
        L.append(f"999 tagged at {tag}." if tag else "999 not tagged today.")

    return L


def format_reminders(p: dict, lines: list[str]) -> str:
    sig = p.get("signal", "")
    head = {"PREMARKET": "🌅 PREMARKET", "CHECK10": "⏱ 10:00 CHECK-IN", "HOD_PUT": "🔻 HOD PUT",
            "LOD_CALL": "🔺 LOD CALL", "RECAP": "📓 RECAP", "TREND_MODE": "🚩 TREND MODE"}.get(sig, sig)
    body = "\n".join(f"• {x}" for x in lines) if lines else "• Nothing notable."
    return f"{head} — {p.get('ticker', 'SPY')} {p.get('time', '')}\nYou've seen this before:\n{body}"


if __name__ == "__main__":
    # Illustrative payloads, not real alert data.
    base = {"ticker": "SPY", "open": "769.90", "pd_high": "769.60", "pd_low": "765.10", "pd_close": "769.00",
            "open_outside": "true", "re_entered": "false", "open_drive": "true", "drive_dir": "UP",
            "att_lod": "0", "att_hod": "5", "failed_lod": "0", "failed_hod": "5",
            "trend_mode": "UP", "trend_at": "10:40", "trend_n": "2", "regime": "BULL",
            "ma200": "773.40", "ma999": "771.50", "tag999_at": "15:58",
            "sess_high": "776.40", "sess_low": "769.50", "close": "773.10"}
    tests = [
        dict(base, signal="PREMARKET", time="09:28", pm_high="738.10", pm_low="735.20", pm_push_high="5", pm_push_low="2"),
        dict(base, signal="CHECK10", time="10:02"),
        dict(base, signal="CHECK10", time="10:02", open="764.20", drive_dir="DOWN", regime="BEAR", ma200="766.00", ma999="767.50"),
        dict(base, signal="HOD_PUT", time="11:14", att_hod="2", failed_hod="1", close="775.00", ma999="771.00"),
        dict(base, signal="LOD_CALL", time="10:30", att_lod="1", failed_lod="0", close="764.00", ma999="767.00"),
        dict(base, signal="RECAP", time="16:00"),
        dict(base, signal="RECAP", time="16:00", open="770.00", close="770.50", sess_high="773.00", sess_low="768.00", open_drive="false", trend_mode="", failed_hod="0", att_hod="1"),
    ]
    for t in tests:
        print(format_reminders(t, build_reminders(t)))
        print()
