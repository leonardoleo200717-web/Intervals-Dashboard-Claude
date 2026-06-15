"""Comprehensive 100-case test suite for the Interval Training Dashboard.

Covers: real FIT parsing (via fit-tool's builder → the actual parse path,
including CRC/UTF-8 handling), structure-string parsing, interval detection
(spec / heuristic / override / drills), the KPI engine, weekly + ACWR + TRIMP,
race predictions, recovery adherence, and every API endpoint with edge cases.

Run: python test_suite.py
"""
import os
import sys
import tempfile
import json

# Isolate the store and fit dir BEFORE importing app does any IO.
_TMP = tempfile.mkdtemp(prefix="dash_test_")
os.environ.pop("ANTHROPIC_API_KEY", None)  # exercise the "AI not configured" path

import app
app.STORE_PATH = os.path.join(_TMP, "sessions.json")
app.FIT_DIR = os.path.join(_TMP, "fit_files")
os.makedirs(app.FIT_DIR, exist_ok=True)

from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.profile_type import Sport, SubSport, LapTrigger

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def section(title):
    RESULTS.append((("== " + title + " =="), None, ""))


# ── helpers ──────────────────────────────────────────────────────────
def build_fit(path, laps, sport=Sport.RUNNING, sub_sport=None, hr=True,
              start=1780000000, trace=True):
    """laps: list of (distance_m, duration_s, hr, LapTrigger)."""
    b = FitFileBuilder(auto_define=True)
    s = SessionMessage()
    s.sport = sport
    if sub_sport is not None:
        s.sub_sport = sub_sport
    s.start_time = start * 1000
    s.total_distance = sum(l[0] for l in laps)
    s.total_elapsed_time = sum(l[1] for l in laps)
    if hr:
        s.avg_heart_rate = 160
    b.add(s)
    t = start
    for dist, dur, lhr, trig in laps:
        if trace and dur > 0:
            for k in range(0, int(dur), 2):
                r = RecordMessage()
                r.timestamp = (t + k) * 1000
                if hr and lhr is not None:
                    r.heart_rate = lhr
                r.speed = (dist / dur if dur else 0)
                b.add(r)
        lm = LapMessage()
        lm.total_distance = dist
        lm.total_elapsed_time = dur
        lm.start_time = t * 1000
        if hr and lhr is not None:
            lm.avg_heart_rate = lhr
            lm.max_heart_rate = lhr + 8
        lm.lap_trigger = trig
        b.add(lm)
        t += int(dur) if dur else 1
    b.build().to_file(path)
    return path


def fit_path(name):
    return os.path.join(app.FIT_DIR, name)


def synth_record(rid, laps, easy=False, track=False, avg_hr=165,
                 date_iso="2026-06-10", structure=None, activity_name=None,
                 zone_seconds=None, trace=None):
    """Build a stored-record dict matching parse_fit's output schema."""
    total_d = sum(l["distance_m"] for l in laps)
    total_t = sum(l["duration_s"] for l in laps)
    return {
        "id": rid, "date_iso": date_iso,
        "date": "10 Jun 2026", "sport": "running",
        "total_distance_m": total_d, "total_elapsed_s": total_t,
        "avg_hr": avg_hr, "laps": laps, "trace": trace or [],
        "hr_at_ref_pace": None, "ref_in_band_seconds": 0,
        "zone_seconds": zone_seconds or [0] * 5,
        "seconds_below_zone2": 0, "trace_seconds": 0,
        "workout_name": None, "activity_name": activity_name, "planned": None,
        "easy": easy, "track": track, "theoretical_target": None,
        "structure": structure, "lap_types": {},
    }


def lap(d, t, hr=160, trig="manual"):
    return {"distance_m": d, "duration_s": t, "hr_avg": hr, "max_hr": (hr + 8) if hr else None,
            "lap_trigger": trig, "start_offset_s": 0}


def interval_laps(n, rep_d, rep_t, rec_d=200, rec_t=80, rep_hr=175, with_wucd=True):
    out = []
    if with_wucd:
        out.append(lap(2000, 600, 130))
    for _ in range(n):
        out.append(lap(rep_d, rep_t, rep_hr))
        out.append(lap(rec_d, rec_t, 140))
    if with_wucd:
        out.append(lap(1500, 480, 125))
    return out


# ════════════════════════════════════════════════════════════════════
# GROUP A — Real FIT parsing (actual parse_fit path)
# ════════════════════════════════════════════════════════════════════
section("A. Real FIT parsing")

# A1: basic distance interval
p = build_fit(fit_path("2026-05-28_Running_1001.fit"),
              [(2000, 600, 130, LapTrigger.MANUAL)] +
              [(400, 90, 175, LapTrigger.DISTANCE), (200, 80, 140, LapTrigger.DISTANCE)] * 5 +
              [(1500, 480, 125, LapTrigger.MANUAL)])
rec, reason = app.parse_fit(p)
check("A1 parse running ok", rec is not None, str(reason))
check("A2 lap count == 12", rec and len(rec["laps"]) == 12, rec and len(rec["laps"]))
check("A3 total distance metres", rec and abs(rec["total_distance_m"] - 6500) < 1, rec and rec["total_distance_m"])
check("A4 avg_hr present", rec and rec["avg_hr"] == 160)
check("A5 trace built (>0 pts)", rec and len(rec["trace"]) > 0, rec and len(rec["trace"]))
check("A6 id from filename stem", rec and rec["id"] == "2026-05-28_Running_1001", rec and rec["id"])
check("A7 date parsed (2026)", rec and "2026" in rec["date"], rec and rec["date"])

v = app.build_session_view(rec, {rec["id"]: rec})
check("A8 distance label 5x400", v["label"] == "5×400 m", v["label"])
check("A9 itype distance", v["itype"] == "distance", v["itype"])
check("A10 5 reps detected", sum(1 for l in v["laps"] if l["type"] == "active") == 5)

# A11: time-based via lap_trigger
p = build_fit(fit_path("2026-05-29_Running_1002.fit"),
              [(2000, 600, 130, LapTrigger.MANUAL)] +
              [(1300, 300, 175, LapTrigger.TIME), (400, 120, 140, LapTrigger.TIME)] * 4 +
              [(1500, 480, 125, LapTrigger.MANUAL)])
rec2, _ = app.parse_fit(p)
v2 = app.build_session_view(rec2, {rec2["id"]: rec2})
check("A11 time itype", v2["itype"] == "time", v2["itype"])
check("A12 time label 4x5min", v2["label"] == "4×5 min", v2["label"])

# A13: missing HR everywhere
p = build_fit(fit_path("2026-05-30_Running_1003.fit"),
              [(2000, 600, None, LapTrigger.MANUAL)] +
              [(400, 90, None, LapTrigger.DISTANCE), (200, 80, None, LapTrigger.DISTANCE)] * 5,
              hr=False)
rec3, _ = app.parse_fit(p)
check("A13 parse no-HR ok", rec3 is not None)
check("A14 avg_hr None", rec3 and rec3["avg_hr"] is None)
v3 = app.build_session_view(rec3, {rec3["id"]: rec3})
check("A15 EF None without HR", v3["ef"] is None)
check("A16 no crash building no-HR view", v3 is not None and v3["label"] is not None)

# A17: cycling rejected
p = build_fit(fit_path("2026-05-31_Cycling_1004.fit"),
              [(5000, 600, 130, LapTrigger.MANUAL)], sport=Sport.CYCLING)
recc, reasonc = app.parse_fit(p)
check("A17 cycling skipped", recc is None and "running" in (reasonc or ""), reasonc)

# A18: treadmill (no GPS records / no speed) but valid distance
p = build_fit(fit_path("2026-06-01_Running_1005.fit"),
              [(400, 90, 175, LapTrigger.DISTANCE), (200, 80, 140, LapTrigger.DISTANCE)] * 4,
              sub_sport=SubSport.TREADMILL, trace=False)
rect, reasont = app.parse_fit(p)
check("A18 treadmill (no records) parses", rect is not None, str(reasont))
check("A19 treadmill trace empty", rect is not None and len(rect["trace"]) == 0)

# A20: stub final lap merged/dropped
p = build_fit(fit_path("2026-06-02_Running_1006.fit"),
              [(2000, 600, 130, LapTrigger.MANUAL)] +
              [(400, 90, 175, LapTrigger.DISTANCE), (200, 80, 140, LapTrigger.DISTANCE)] * 3 +
              [(5, 3, 120, LapTrigger.SESSION_END)])  # stub
recs, _ = app.parse_fit(p)
check("A20 stub lap removed", recs is not None and all(l["duration_s"] >= 10 for l in recs["laps"]),
      recs and [l["duration_s"] for l in recs["laps"]])

# A21: generic sport with running paces accepted
p = build_fit(fit_path("2026-06-03_Generic_1007.fit"),
              [(1000, 240, 150, LapTrigger.DISTANCE)] * 4, sport=Sport.GENERIC)
recg, reasong = app.parse_fit(p)
check("A21 generic+running-pace accepted", recg is not None, str(reasong))

# A22: import via import_fit_path + duplicate
store = {}
st1 = app.import_fit_path(fit_path("2026-05-28_Running_1001.fit"), store)
st2 = app.import_fit_path(fit_path("2026-05-28_Running_1001.fit"), store)
check("A22 import ok", st1 == "ok", st1)
check("A23 duplicate detected", st2 == "duplicate", st2)


# ════════════════════════════════════════════════════════════════════
# GROUP B — Structure-string parsing
# ════════════════════════════════════════════════════════════════════
section("B. Structure-string parsing")
cases = [
    ("5x5'", "time", 5, 300, None, None),
    ("10x90\"", "time", 10, 90, None, None),
    ("5x4km p1'", "distance", 5, 4000, 60, "time"),
    ("6x1km", "distance", 6, 1000, None, None),
    ("4x15min", "time", 4, 900, None, None),
    ("10x400m", "distance", 10, 400, None, None),
    ("8x1:30 r2'", "time", 8, 90, 120, "time"),
    ("12x400m rec200m", "distance", 12, 400, 200, "distance"),
    ("3x2000m", "distance", 3, 2000, None, None),
    ("5×1000m p90\"", "distance", 5, 1000, 90, "time"),  # unicode ×
    ("Track 8x400m", "distance", 8, 400, None, None),
    ("easy run", None, None, None, None, None),
    ("", None, None, None, None, None),
    ("nonsense text", None, None, None, None, None),
    ("5x3min r1km", "time", 5, 180, 1000, "distance"),
]
for i, (txt, it, rc, rt, rec_t, rec_it) in enumerate(cases, 1):
    spec = app.parse_structure_string(txt)
    if it is None:
        check(f"B{i} '{txt}' -> None", spec is None, spec)
    else:
        ok = spec and spec["itype"] == it and spec["rep_count"] == rc and abs(spec["rep_target"] - rt) < 1
        check(f"B{i} '{txt}' parsed", ok, spec)
        if rec_t is not None:
            check(f"B{i}r recovery '{txt}'", spec and spec["recovery_target"] == rec_t and spec["recovery_itype"] == rec_it,
                  spec and (spec.get("recovery_target"), spec.get("recovery_itype")))


# ════════════════════════════════════════════════════════════════════
# GROUP C — Interval detection
# ════════════════════════════════════════════════════════════════════
section("C. Interval detection")

# C1: spec-guided excludes drills
laps = [lap(2000, 600, 130), lap(100, 25, 150), lap(100, 24, 150), lap(100, 26, 150)] + \
       sum([[lap(400, 90, 175), lap(200, 80, 140)] for _ in range(5)], []) + [lap(1500, 480, 125)]
spec = app.parse_structure_string("5x400m")
out, it = app.detect_intervals(laps, True, spec)
actives = [l for l in out if l["type"] == "active"]
check("C1 drills excluded (5 reps)", len(actives) == 5, len(actives))
check("C2 drills are wu not active", all(out[i]["type"] in ("wu",) for i in (1, 2, 3)),
      [out[i]["type"] for i in (1, 2, 3)])
check("C3 reps are 400m", all(abs(a["distance_m"] - 400) < 1 for a in actives))

# C4: heuristic fallback (no spec)
out2, it2 = app.detect_intervals(interval_laps(5, 400, 90), True, None)
check("C4 heuristic finds 5 reps", sum(1 for l in out2 if l["type"] == "active") == 5,
      sum(1 for l in out2 if l["type"] == "active"))
check("C5 heuristic WU first", out2[0]["type"] == "wu")
check("C6 heuristic CD last", out2[-1]["type"] == "cd")

# C7: per-lap override wins
out3, _ = app.detect_intervals(interval_laps(5, 400, 90), True,
                               app.parse_structure_string("5x400m"), {"1": "drill"})
check("C7 override forces drill", out3[1]["type"] == "drill", out3[1]["type"])
check("C8 override drops a rep", sum(1 for l in out3 if l["type"] == "active") == 4,
      sum(1 for l in out3 if l["type"] == "active"))

# C9: no laps
out4, it4 = app.detect_intervals([], True, None)
check("C9 empty laps no crash", out4 == [] and it4 is None)

# C10: all laps identical (no clear WU/CD)
out5, _ = app.detect_intervals([lap(400, 90, 175)] * 6, True, app.parse_structure_string("6x400m"))
check("C10 6 identical reps", sum(1 for l in out5 if l["type"] == "active") == 6,
      sum(1 for l in out5 if l["type"] == "active"))

# C11: zero-distance lap doesn't divide-by-zero
out6, _ = app.detect_intervals([lap(2000, 600, 130), lap(0, 30, 120), lap(400, 90, 175)], True, None)
check("C11 zero-distance lap safe", out6 is not None)

# C12: rep numbering sequential
out7, _ = app.detect_intervals(interval_laps(4, 1000, 240), False, app.parse_structure_string("4x1km"))
reps = [l.get("rep") for l in out7 if l["type"] == "active"]
check("C12 reps numbered 1..4", reps == [1, 2, 3, 4], reps)

# C13: spec with no target falls back to fastest-N
out8, _ = app.detect_intervals(interval_laps(3, 800, 170), False, {"rep_count": 3, "itype": "distance", "rep_target": None})
check("C13 no-target spec picks 3", sum(1 for l in out8 if l["type"] == "active") == 3,
      sum(1 for l in out8 if l["type"] == "active"))


# --- Garmin per-lap intensity markers (read straight from the FIT) -----
def lap_i(d, t, intensity, hr=160):
    lp = lap(d, t, hr)
    lp["intensity"] = intensity
    return lp

# C14: intensity markers classify wu/active/recovery/cd with NO spec at all
ilaps = [lap_i(2000, 600, "warmup", 130),
         lap_i(400, 90, "active", 175), lap_i(200, 80, "rest", 140),
         lap_i(400, 91, "active", 176), lap_i(200, 82, "rest", 141),
         lap_i(400, 92, "active", 177), lap_i(200, 81, "rest", 140),
         lap_i(1500, 480, "cooldown", 125)]
o14, _ = app.detect_intervals(ilaps, True, None)
check("C14 intensity finds 3 reps (no spec)",
      sum(1 for l in o14 if l["type"] == "active") == 3,
      [l["type"] for l in o14])
check("C14b intensity WU first", o14[0]["type"] == "wu", o14[0]["type"])
check("C14c intensity CD last", o14[-1]["type"] == "cd", o14[-1]["type"])
check("C14d intensity rests are recovery",
      o14[2]["type"] == "recovery" and o14[4]["type"] == "recovery")

# C15: drills tagged warmup stay out even when same distance as the reps
dlaps = [lap_i(2000, 600, "warmup", 130),
         lap_i(400, 110, "warmup", 150), lap_i(400, 112, "warmup", 150),  # strides/drills
         lap_i(400, 90, "active", 175), lap_i(200, 80, "rest", 140),
         lap_i(400, 91, "active", 176), lap_i(200, 82, "rest", 141),
         lap_i(1500, 480, "cooldown", 125)]
o15, _ = app.detect_intervals(dlaps, True, None)
check("C15 same-distance drills excluded",
      sum(1 for l in o15 if l["type"] == "active") == 2,
      [l["type"] for l in o15])

# C16: all-active markers are NOT meaningful (plain free run) → heuristic
flat = [lap_i(400, 90, "active") for _ in range(5)]
check("C16 all-active not meaningful", app._has_meaningful_lap_intensity(flat) is False)
check("C16b mixed markers are meaningful", app._has_meaningful_lap_intensity(ilaps) is True)

# C17: a slow-but-tagged rep stays active (intensity beats pace guessing)
slow = [lap_i(2000, 600, "warmup", 130),
        lap_i(400, 90, "active", 175), lap_i(200, 80, "rest", 140),
        lap_i(400, 130, "active", 165), lap_i(200, 82, "rest", 141),  # tired/slow rep
        lap_i(400, 91, "active", 176), lap_i(200, 81, "rest", 140),
        lap_i(1500, 480, "cooldown", 125)]
o17, _ = app.detect_intervals(slow, True, None)
check("C17 slow tagged rep kept active",
      o17[3]["type"] == "active" and sum(1 for l in o17 if l["type"] == "active") == 3,
      [l["type"] for l in o17])

# C18: a typed (manual) structure still overrides intensity markers, but a
# title-derived (name) one does not — device markers are more reliable.
manual18 = app.parse_structure_string("2x400m"); manual18["source"] = "manual"
o18, _ = app.detect_intervals(ilaps, True, manual18)
check("C18 manual spec overrides intensity",
      sum(1 for l in o18 if l["type"] == "active") == 2,
      sum(1 for l in o18 if l["type"] == "active"))
o18b, _ = app.detect_intervals(ilaps, True, app.parse_structure_string("2x400m"))
check("C18b name spec does NOT override intensity (device wins)",
      sum(1 for l in o18b if l["type"] == "active") == 3,
      sum(1 for l in o18b if l["type"] == "active"))

# C19: per-lap override still wins over intensity
o19, _ = app.detect_intervals(ilaps, True, None, {"1": "drill"})
check("C19 per-lap override beats intensity", o19[1]["type"] == "drill", o19[1]["type"])


# ════════════════════════════════════════════════════════════════════
# GROUP D — KPI engine
# ════════════════════════════════════════════════════════════════════
section("D. KPI engine")

# D1: EF monotonic — faster at same HR → higher EF
ef_slow = app.efficiency_factor(10000, 3000, 150)
ef_fast = app.efficiency_factor(11000, 3000, 150)
check("D1 EF rises when faster", ef_fast > ef_slow, (ef_slow, ef_fast))
check("D2 EF None no HR", app.efficiency_factor(10000, 3000, None) is None)

# D3: decoupling only on easy sessions
easy_rec = synth_record("easy1", [lap(3000, 900, 140), lap(3000, 920, 145)], easy=True)
ev = app.build_session_view(easy_rec, {})
check("D3 easy has decoupling", ev["decoupling"] is not None, ev["decoupling"])
check("D4 easy label", ev["label"] == "Easy run")
int_rec = synth_record("int1", interval_laps(5, 400, 90))
iv = app.build_session_view(int_rec, {})
check("D5 interval suppresses decoupling", iv["decoupling"] is None, iv["decoupling"])
check("D6 interval has pace_fade", iv["pace_fade"] is not None)
check("D7 interval has pace_cv", iv["pace_cv"] is not None)

# D8: pace fade sign — slowing reps → positive
slow_laps = [lap(2000, 600, 130)] + sum([[lap(400, 88 + i * 3, 175), lap(200, 80, 140)] for i in range(5)], []) + [lap(1500, 480, 125)]
fv = app.build_session_view(synth_record("fade1", slow_laps), {})
check("D8 pace fade positive when slowing", fv["pace_fade"] > 0, fv["pace_fade"])

# D9: pace CV near zero for identical reps
even_rec = synth_record("even1", interval_laps(5, 400, 90))
evv = app.build_session_view(even_rec, {})
check("D9 pace CV ~0 even reps", evv["pace_cv"] < 1.0, evv["pace_cv"])

# D10: HRR60 from trace
trace = [{"t": i, "hr": 175} for i in range(0, 91)] + [{"t": 90 + i, "hr": 175 - i} for i in range(1, 61)]
hr_rec = synth_record("hrr1", [lap(2000, 600, 130), lap(400, 90, 175), lap(200, 80, 140)],
                      trace=trace)
hr_rec["laps"][1]["start_offset_s"] = 0
hrv = app.build_session_view(hr_rec, {})
check("D10 HRR60 computed from trace", hrv["hrr60_avg"] is not None, hrv["hrr60_avg"])

# D11: RQS fallback when no trace
no_trace = synth_record("rqs1", interval_laps(4, 400, 90))
rv = app.build_session_view(no_trace, {})
check("D11 RQS fallback present", rv["rqs_avg"] is not None, rv["rqs_avg"])

# D12: SPS-I always computable, SPS-T null without target
check("D12 SPS-I computed", iv["sps_i"] is not None, iv["sps_i"])
check("D13 SPS-T null without target", iv["sps_t"] is None)

# D14: SPS-T populated with theoretical target
tgt_rec = synth_record("tgt1", interval_laps(5, 400, 90))
tgt_rec["theoretical_target"] = "1:30"
tv = app.build_session_view(tgt_rec, {})
check("D14 SPS-T populated with target", tv["sps_t"] is not None, tv["sps_t"])

# D15: score functions bounded 0..100
for d in (-20, -3, 0, 3, 10, 50):
    s = app.pace_score(d)
    check(f"D15 pace_score({d}) in 0..100", 0 <= s <= 100, s)

# D16: cardiac cost present per active rep
cc = [l.get("cardiac_cost") for l in iv["laps"] if l["type"] == "active"]
check("D16 cardiac cost computed", any(c is not None for c in cc), cc)


# ════════════════════════════════════════════════════════════════════
# GROUP E — Weekly / ACWR / TRIMP
# ════════════════════════════════════════════════════════════════════
section("E. Weekly / ACWR / TRIMP")
wk_store = {}
# 4 weeks of data, increasing volume
import datetime as _dt
base = _dt.date(2026, 5, 4)  # a Monday
for w in range(4):
    for d in range(3):
        day = base + _dt.timedelta(days=w * 7 + d * 2)
        rid = f"wk_{w}_{d}"
        laps = interval_laps(5, 400, 90) if d == 0 else [lap(8000, 2400, 140)]
        r = synth_record(rid, laps, easy=(d != 0), date_iso=day.isoformat(),
                         zone_seconds=[300, 600, 600, 300, 60])
        wk_store[rid] = r
weekly = app.compute_weekly(wk_store)
check("E1 weekly returns weeks", "weeks" in weekly and len(weekly["weeks"]) == 8, len(weekly.get("weeks", [])))
nonempty = [w for w in weekly["weeks"] if w["session_count"] > 0]
check("E2 4 non-empty weeks", len(nonempty) == 4, len(nonempty))
check("E3 weekly total_km > 0", all(w["total_km"] > 0 for w in nonempty))
check("E4 ISO Monday weeks", all(_dt.date.fromisoformat(w["week_start"]).weekday() == 0 for w in nonempty))
check("E5 WoW pct computed", any(w.get("wow_km_pct") is not None for w in nonempty))
check("E6 easy_ratio present", all(w.get("easy_ratio") is not None for w in nonempty))
check("E7 TRIMP computed", all(w.get("trimp", 0) > 0 for w in nonempty))
check("E8 ACWR attached", all("acwr_status" in w for w in nonempty))
check("E9 session split counts", all(w["interval_count"] + w["easy_count"] == w["session_count"] for w in nonempty))

# E10: WoW alert fires on big jump
spike_store = {
    "s1": synth_record("s1", [lap(5000, 1500, 140)], easy=True, date_iso="2026-05-04"),
    "s2": synth_record("s2", [lap(20000, 6000, 140)], easy=True, date_iso="2026-05-11"),
}
sw = app.compute_weekly(spike_store)
spike_week = [w for w in sw["weeks"] if w["session_count"] > 0][-1]
check("E10 WoW alert on 4x jump", spike_week.get("wow_alert") is True, spike_week.get("wow_km_pct"))


# ════════════════════════════════════════════════════════════════════
# GROUP F — Predictions + recovery adherence
# ════════════════════════════════════════════════════════════════════
section("F. Predictions / recovery adherence")
pred_store = {"p1": synth_record("p1", interval_laps(5, 4000, 900, rec_d=400, rec_t=95),
                                 activity_name="5x4km r400m")}
pred = app.compute_race_predictions(pred_store)
check("F1 predictions returned", pred is not None)
check("F2 four race distances", pred and len(pred["predictions"]) == 4)
check("F3 marathon predicted", pred and any(p["name"] == "Marathon" for p in pred["predictions"]))
check("F4 anchor is longest rep", pred and pred["anchor"]["distance_m"] == 4000, pred and pred["anchor"]["distance_m"])
check("F5 confidence label", pred and pred["confidence"] in ("high", "medium", "low"))
check("F6 marathon delta numeric", pred and isinstance(pred["marathon_delta_s"], (int, float)))
# F7: no candidate -> None
check("F7 no anchor -> None", app.compute_race_predictions(
    {"e": synth_record("e", [lap(5000, 1500, 140)], easy=True)}) is None)
# F8: recovery adherence
rv2 = app.build_session_view(pred_store["p1"], pred_store)
check("F8 recovery adherence computed", rv2["recovery_adherence"] is not None, rv2["recovery_adherence"])
check("F9 recovery ~100% (400 vs 400)", abs(rv2["recovery_adherence"] - 100) < 30, rv2["recovery_adherence"])
# F10: no recovery target -> None
rv3 = app.build_session_view(synth_record("nr", interval_laps(5, 400, 90)), {})
check("F10 no rec target -> adherence None", rv3["recovery_adherence"] is None)


# ════════════════════════════════════════════════════════════════════
# GROUP G — API endpoints (Flask test client)
# ════════════════════════════════════════════════════════════════════
section("G. API endpoints")
# seed store
api_store = {
    "a1": synth_record("a1", interval_laps(5, 400, 90), activity_name="5x400m", date_iso="2026-06-01"),
    "a2": synth_record("a2", [lap(8000, 2400, 140)], easy=True, date_iso="2026-06-03"),
}
app.save_store(api_store)
c = app.app.test_client()

check("G1 GET / serves html", c.get("/").status_code == 200 and b"<html" in c.get("/").data.lower())
r = c.get("/api/sessions")
check("G2 GET sessions 200", r.status_code == 200 and len(r.get_json()) == 2, r.status_code)
check("G3 sessions newest first", r.get_json()[0]["date_iso"] >= r.get_json()[1]["date_iso"])
check("G4 GET one session", c.get("/api/sessions/a1").status_code == 200)
check("G5 GET missing 404", c.get("/api/sessions/nope").status_code == 404)

# PATCH flags + recompute
pr = c.patch("/api/sessions/a1", json={"easy": True})
check("G6 PATCH easy recomputes", pr.status_code == 200 and pr.get_json()["label"] == "Easy run")
c.patch("/api/sessions/a1", json={"easy": False})
pr2 = c.patch("/api/sessions/a1", json={"structure": "10x400m"})
check("G7 PATCH structure overrides", pr2.get_json()["structure_source"] == "manual", pr2.get_json().get("structure_source"))
c.patch("/api/sessions/a1", json={"structure": None})
pr3 = c.patch("/api/sessions/a1", json={"lap_types": {"1": "drill"}})
check("G8 PATCH lap_types accepted", pr3.status_code == 200)
# invalid lap_type value ignored
pr4 = c.patch("/api/sessions/a1", json={"lap_types": {"1": "bogus"}})
check("G9 invalid lap_type ignored", pr4.status_code == 200 and "1" not in app.load_store()["a1"]["lap_types"])
c.patch("/api/sessions/a1", json={"lap_types": {}})

# persistence survives reload
check("G10 PATCH persisted", app.load_store()["a1"].get("easy") is False)

# weekly / config / predictions / alerts
check("G11 GET weekly 200", c.get("/api/weekly").status_code == 200)
cfg = c.get("/api/config").get_json()
check("G12 config exposes weekly_km_target", cfg.get("weekly_km_target") == app.config.USER_PROFILE["weekly_km_target"])
check("G13 config acwr bounds", cfg.get("acwr_low") and cfg.get("acwr_high"))
check("G14 GET predictions 200", c.get("/api/predictions").status_code == 200)
check("G15 GET alerts 200", "alerts" in c.get("/api/alerts").get_json())

# CSV export
csv = c.get("/api/sessions/a1/export.csv")
check("G16 CSV export", csv.status_code == 200 and b"type,rep" in csv.data)
check("G17 CSV 404 missing", c.get("/api/sessions/nope/export.csv").status_code == 404)

# chat without key -> 503 exact message
chat = c.post("/api/chat", json={"session_id": "a1", "message": "hi"})
check("G18 chat 503 no key", chat.status_code == 503 and "AI not configured" in chat.get_json().get("error", ""),
      chat.get_json())
chat_s = c.post("/api/chat/stream", json={"session_id": "a1", "message": "hi"})
check("G19 chat stream 503 no key", chat_s.status_code == 503)

# upload bad / empty
check("G20 upload no files 400", c.post("/api/upload", data={}).status_code == 400)

# scan imports the generated fit files
scan = c.post("/api/scan")
check("G21 scan returns structure", scan.status_code == 200 and "added" in scan.get_json())

# malformed PATCH body doesn't crash
check("G22 PATCH empty body ok", c.patch("/api/sessions/a1", json={}).status_code == 200)

# context prompt builds without crash
view_a1 = app.build_session_view(app.load_store()["a1"], app.load_store())
check("G23 chat context prompt builds", isinstance(app.build_context_prompt(view_a1, app.load_store()), str))

# --- multi-provider AI selection (no real network calls) ---------------
# No keys at all: nothing offered, default is None.
for v in ("ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(v, None)
check("G24 no providers configured", app.configured_ai_providers() == [])
cfg0 = c.get("/api/config").get_json()
check("G24b config ai_providers empty", cfg0.get("ai_providers") == [] and cfg0.get("ai_default") is None,
      (cfg0.get("ai_providers"), cfg0.get("ai_default")))

# Only DeepSeek keyed: it is offered, becomes the effective default, and
# _resolve_ai falls back to it even though DEFAULT_AI_PROVIDER is anthropic.
os.environ["DEEPSEEK_API_KEY"] = "sk-test-deepseek"
check("G25 deepseek listed", app.configured_ai_providers() == ["deepseek"], app.configured_ai_providers())
cfg1 = c.get("/api/config").get_json()
check("G25b config offers deepseek only",
      [p["id"] for p in cfg1["ai_providers"]] == ["deepseek"] and cfg1["ai_default"] == "deepseek",
      cfg1.get("ai_default"))
check("G25c no API key leaked", all("env_key" not in p and "key" not in p for p in cfg1["ai_providers"]))
prov, key, model = app._resolve_ai({})
check("G25d resolve falls back to deepseek",
      prov["kind"] == "openai" and key == "sk-test-deepseek" and model == "deepseek-chat",
      (prov.get("kind"), model))

# Explicit provider + model in the request is honoured; missing key → 503.
os.environ["ANTHROPIC_API_KEY"] = "sk-test-anthropic"
prov2, key2, model2 = app._resolve_ai({"provider": "anthropic", "model": "claude-opus-4-8"})
check("G26 explicit provider/model honoured",
      prov2["kind"] == "anthropic" and key2 == "sk-test-anthropic" and model2 == "claude-opus-4-8",
      (prov2.get("kind"), model2))
r_no_key = app._resolve_ai({"provider": "openai"})
check("G26b unkeyed provider → 503", r_no_key[0] is None and r_no_key[2] == 503 and "OPENAI_API_KEY" in r_no_key[1],
      r_no_key)

# OpenAI-compatible payload puts the system prompt as a leading message.
payload = json.loads(app._openai_payload("deepseek-chat", "SYS", [{"role": "user", "content": "hi"}]))
check("G27 openai payload shape",
      payload["model"] == "deepseek-chat" and payload["messages"][0] == {"role": "system", "content": "SYS"}
      and payload["messages"][1]["content"] == "hi",
      payload)

# Clean up so later groups see no AI keys.
for v in ("ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(v, None)


# ════════════════════════════════════════════════════════════════════
# GROUP H — Adversarial / edge / regression
# ════════════════════════════════════════════════════════════════════
section("H. Adversarial / edge / regression")

# H1: corrupted CRC must still parse (regression for the check_crc=False fix)
src = fit_path("2026-05-28_Running_1001.fit")
with open(src, "rb") as fh:
    data = bytearray(fh.read())
data[-1] ^= 0xFF  # break the trailing CRC
data[-2] ^= 0xFF
corrupt = fit_path("2026-06-09_Running_2001.fit")
with open(corrupt, "wb") as fh:
    fh.write(data)
try:
    rcc, rsc = app.parse_fit(corrupt)
    check("H1 corrupted-CRC file still parses", rcc is not None, str(rsc))
except Exception as e:
    check("H1 corrupted-CRC file still parses", False, f"raised {e}")

# H2: HR @ reference pace populates from a steady in-band trace (4:00/km)
p = build_fit(fit_path("2026-06-08_Running_2002.fit"),
              [(1666, 400, 150, LapTrigger.MANUAL)])  # 400 s @ 240 s/km (in 235-245 band)
rref, _ = app.parse_fit(p)
check("H2 HR@refpace computed from trace", rref and rref["hr_at_ref_pace"] is not None,
      rref and (rref["hr_at_ref_pace"], rref["ref_in_band_seconds"]))
check("H3 HR@refpace ≈ 150", rref and rref["hr_at_ref_pace"] and abs(rref["hr_at_ref_pace"] - 150) < 2,
      rref and rref["hr_at_ref_pace"])

# H4: out-of-band pace yields no HR@refpace
p = build_fit(fit_path("2026-06-07_Running_2003.fit"),
              [(2000, 360, 150, LapTrigger.MANUAL)])  # 3:00/km, far below band
rob, _ = app.parse_fit(p)
check("H4 out-of-band -> hr_at_ref None", rob and rob["hr_at_ref_pace"] is None,
      rob and rob["hr_at_ref_pace"])

# H5: zone breakdown sums correctly
trace_z = [{"t": i, "hr": 100 + (i % 90)} for i in range(600)]
zs, below, tot = app._zone_breakdown(trace_z)
check("H5 zone seconds sum == total", sum(zs) == tot == 600, (sum(zs), tot))
check("H6 below-z2 count plausible", 0 <= below <= 600, below)
check("H7 five zones", len(zs) == 5, len(zs))

# H8-H13: distance rounding table
check("H8 400 track -> 400", app.round_distance(400, True) == (400, True))
check("H9 393 road -> 400", app.round_distance(393, False) == (400, True))
check("H10 720 track -> raw (no match)", app.round_distance(720, True) == (720, False),
      app.round_distance(720, True))
check("H11 4000 -> raw (above max)", app.round_distance(4000, True)[1] is False)
check("H12 1000 track -> 1000", app.round_distance(1000, True) == (1000, True))
check("H13 200 -> not crash", isinstance(app.round_distance(200, True), tuple))

# H14: mixed-distance reps → "(mixed)" label, no spec
mixed_laps = [lap(2000, 600, 130), lap(400, 90, 175), lap(200, 80, 140),
              lap(800, 180, 176), lap(200, 80, 140), lap(1200, 280, 177), lap(1500, 480, 125)]
mv = app.build_session_view(synth_record("mixed1", mixed_laps), {})
check("H14 mixed reps label", "mixed" in (mv["label"] or ""), mv["label"])

# H15: time label formatting for 90s reps via spec
lbl = app._build_label([lap(400, 90, 175)] * 10, "time", False, app.parse_structure_string("10x90\""))
check("H15 90s time label sensible", "10×" in lbl, lbl)

# H16-H19: normalize_enum
check("H16 enum None -> ''", app.normalize_enum(None) == "")
check("H17 LapTrigger enum -> token", app.normalize_enum(LapTrigger.TIME, LapTrigger) == "time",
      app.normalize_enum(LapTrigger.TIME, LapTrigger))
check("H18 string with prefix stripped", app.normalize_enum("LapTrigger.DISTANCE") == "distance")
check("H19 int code resolved", isinstance(app.normalize_enum(0, LapTrigger), str))

# H20-H24: format helpers
check("H20 fmt_pace None", app.fmt_pace(None) == "—")
check("H21 fmt_pace 240 -> 4:00", app.fmt_pace(240) == "4:00", app.fmt_pace(240))
check("H22 fmt_pace rounds 239.6s", app.fmt_pace(239.6) == "4:00", app.fmt_pace(239.6))
check("H23 fmt_duration h:mm:ss", app.fmt_duration(3661) == "1:01:01", app.fmt_duration(3661))
check("H24 pace_sec_km guards tiny distance", app.pace_sec_km(40, 30) is None)

# H25: single-lap session (continuous tempo) doesn't crash
sv1 = app.build_session_view(synth_record("single1", [lap(8000, 1920, 168)]), {})
check("H25 single-lap interval no crash", sv1 is not None and sv1["label"] is not None, sv1["label"])

# H26: easy run excluded from predictions but counted in weekly
mixed_store = {
    "i": synth_record("i", interval_laps(5, 2000, 420, rec_d=400, rec_t=120), date_iso="2026-06-01"),
    "e": synth_record("e", [lap(10000, 3000, 140)], easy=True, date_iso="2026-06-02"),
}
predm = app.compute_race_predictions(mixed_store)
check("H26 predictions ignore easy", predm and predm["anchor"]["distance_m"] == 2000,
      predm and predm["anchor"]["distance_m"])

# H27: theoretical target as mm:ss for distance pace
tr = synth_record("tt", interval_laps(5, 1000, 210, rep_hr=175))
tr["theoretical_target"] = "3:30"
tvv = app.build_session_view(tr, {})
deltas = [l.get("delta_t") for l in tvv["laps"] if l["type"] == "active"]
check("H27 delta vs theoretical computed", any(d is not None for d in deltas), deltas)

# H28: store survives save/load round-trip with unicode
app.save_store({"u": synth_record("u", interval_laps(3, 400, 90), activity_name="5×400m café")})
check("H28 unicode store round-trips", app.load_store()["u"]["activity_name"] == "5×400m café")

# H29: alerts computed without crash on rich store
al = app.compute_alerts(wk_store)
check("H29 alerts list returned", isinstance(al, list))

# H30: weekly with empty store
check("H30 weekly empty store ok", app.compute_weekly({})["weeks"] is not None)

# H31: reps and recoveries share the same distance (200m hard / 200m float)
# plus 200m drills — must pick the FAST reps, not drills/recoveries.
s4 = [lap(2000, 600, 130), lap(200, 40, 150), lap(200, 41, 150)] + \
     sum([[lap(200, 38, 178), lap(200, 90, 140)] for _ in range(5)], []) + [lap(1500, 480, 125)]
o4, _ = app.detect_intervals(s4, True, app.parse_structure_string("5x200m"))
act_durs = [l["duration_s"] for l in o4 if l["type"] == "active"]
check("H31 same-distance reps: picks fast efforts", act_durs == [38] * 5, act_durs)

# H32: recoveries near rep distance (300m rec vs 400m reps) still isolates reps
s1 = [lap(2000, 600, 130)] + sum([[lap(398 + i, 90, 175), lap(300, 110, 140)] for i in range(10)], []) + [lap(1500, 480, 125)]
o1c, _ = app.detect_intervals(s1, True, app.parse_structure_string("10x400m"))
check("H32 near-distance recoveries excluded", sum(1 for l in o1c if l["type"] == "active") == 10,
      sum(1 for l in o1c if l["type"] == "active"))


# ── report ───────────────────────────────────────────────────────────
passed = sum(1 for _, ok, _ in RESULTS if ok is True)
failed = [(n, d) for n, ok, d in RESULTS if ok is False]
total = passed + len(failed)
for name, ok, detail in RESULTS:
    if ok is None:
        print(f"\n{name}")
    elif ok:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}   [{detail}]")
print("=" * 64)
print(f"TOTAL CASES: {total}  |  PASSED: {passed}  |  FAILED: {len(failed)}")
if failed:
    print("\nFAILURES:")
    for n, d in failed:
        print(f"  - {n}: {d}")
sys.exit(1 if failed else 0)
