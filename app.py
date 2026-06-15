"""Interval Training Dashboard — Flask backend.

Responsibilities:
  * Parse Garmin FIT files (fit-tool) into a normalised session record.
  * Compute the full KPI suite (session, per-lap, weekly).
  * Persist sessions to sessions.json with atomic writes.
  * Expose the REST API consumed by static/index.html.
  * Proxy the AI chatbot to the Anthropic API (key stays server-side).

Design note: the immutable parsed data (meta, raw laps, HR trace and a few
flag-independent scalars) is stored per session. Everything KPI-shaped is
recomputed on demand by build_session_view(), so a flag PATCH simply changes
the stored flags and re-derives the view — no stale numbers can survive.
"""

import csv
import io
import math
import os
import statistics
import tempfile
from datetime import datetime, timezone, timedelta

from flask import Flask, jsonify, request, send_from_directory, Response
from dotenv import load_dotenv

import config

load_dotenv()

# fit-tool imports are wrapped so the app still boots (and reports honestly)
# if the dependency is missing.
try:
    from fit_tool.fit_file import FitFile
    from fit_tool.profile.messages.session_message import SessionMessage
    from fit_tool.profile.messages.lap_message import LapMessage
    from fit_tool.profile.messages.record_message import RecordMessage
    from fit_tool.profile.profile_type import Sport, SubSport, LapTrigger

    # Structured-workout messages are optional: only present when the run was
    # done following a planned Garmin workout. Import defensively so a fit-tool
    # build without these classes still parses ordinary activities.
    try:
        from fit_tool.profile.messages.workout_message import WorkoutMessage
        from fit_tool.profile.messages.workout_step_message import WorkoutStepMessage
        from fit_tool.profile.profile_type import Intensity, WorkoutStepDuration
    except Exception:  # pragma: no cover
        WorkoutMessage = WorkoutStepMessage = None
        Intensity = WorkoutStepDuration = None

    # fit-tool decodes FIT string fields with a strict bytes.decode('utf-8').
    # Real Garmin files routinely pad string fields with 0xff or carry
    # non-UTF-8 bytes (device/product names, developer field names), which
    # makes the strict decode raise UnicodeDecodeError and abort the whole
    # file. Patch the string reader to decode leniently (bad bytes → U+FFFD)
    # so the numeric data we actually use still parses.
    from fit_tool.field import Field as _FitField

    def _lenient_read_strings_from_bytes(self, bytes_buffer):
        string_container = bytes_buffer.decode("utf-8", errors="replace")
        strings = string_container.split("\x00")[:-1]
        self.encoded_values = [s for s in strings if s]

    _FitField.read_strings_from_bytes = _lenient_read_strings_from_bytes

    FIT_TOOL_AVAILABLE = True
except Exception:  # pragma: no cover - only when dependency missing
    FIT_TOOL_AVAILABLE = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORE_PATH = os.path.join(BASE_DIR, "sessions.json")
FIT_DIR = os.path.join(BASE_DIR, "fit_files")
STATIC_DIR = os.path.join(BASE_DIR, "static")

os.makedirs(FIT_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB — allow batch uploads

# Anthropic model for the AI chat. The spec's claude-sonnet-4-20250514 (Sonnet 4.0)
# retires 2026-06-15; this is the current Sonnet ("or newer" per CLAUDE.md §13).
CHAT_MODEL = "claude-sonnet-4-6"

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ════════════════════════════════════════════════════════════════════
# Persistence — atomic JSON store
# ════════════════════════════════════════════════════════════════════
import json


def load_store():
    if not os.path.exists(STORE_PATH):
        return {}
    try:
        with open(STORE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_store(store):
    """Atomic write: temp file in the same dir, then os.replace()."""
    fd, tmp = tempfile.mkstemp(dir=BASE_DIR, prefix=".sessions_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(store, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, STORE_PATH)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


# ════════════════════════════════════════════════════════════════════
# Small helpers
# ════════════════════════════════════════════════════════════════════
def normalize_enum(val, enum_cls=None):
    """lap_trigger / sport come back as enums, ints, or strings.

    Resolve integer codes through the provided fit-tool enum class when given,
    then return a lowercase token stripped of any 'EnumName.' prefix.
    """
    if val is None:
        return ""
    if enum_cls is not None and isinstance(val, (int, float)) and not isinstance(val, bool):
        try:
            return enum_cls(int(val)).name.strip().lower()
        except (ValueError, KeyError):
            return str(val).strip().lower()
    s = str(val).strip().lower()
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    return s


def to_epoch_seconds(ts):
    """Normalise a FIT timestamp (datetime | epoch s | epoch ms) to seconds."""
    if ts is None:
        return None
    if hasattr(ts, "isoformat"):
        try:
            return ts.replace(tzinfo=ts.tzinfo or timezone.utc).timestamp()
        except Exception:
            return ts.timestamp()
    try:
        v = float(ts)
    except (TypeError, ValueError):
        return None
    # Heuristic: values above ~1e12 are milliseconds.
    if v > 1e12:
        v /= 1000.0
    return v


def pace_sec_km(distance_m, duration_s):
    if not distance_m or distance_m <= 50 or not duration_s:
        return None
    return duration_s / (distance_m / 1000.0)


def fmt_pace(sec_km):
    if sec_km is None:
        return "—"
    m = int(sec_km // 60)
    s = int(round(sec_km - m * 60))
    if s == 60:
        m += 1
        s = 0
    return f"{m}:{s:02d}"


def fmt_duration(seconds):
    if seconds is None:
        return "—"
    seconds = int(round(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _get(msg, *names):
    """Return the first present, non-None attribute from a fit-tool message."""
    for n in names:
        if hasattr(msg, n):
            try:
                v = getattr(msg, n)
            except Exception:
                v = None
            if v is not None:
                return v
    return None


# ════════════════════════════════════════════════════════════════════
# FIT parsing
# ════════════════════════════════════════════════════════════════════
RUNNING_TOKENS = {"running", "track_running", "treadmill_running", "trail_running"}


def parse_fit(path):
    """Parse a FIT file into a normalised record.

    Returns (record, None) on success or (None, reason) when the file is
    skipped (e.g. not a running activity).
    """
    if not FIT_TOOL_AVAILABLE:
        raise RuntimeError("fit-tool is not installed — run: pip install fit-tool")

    # FitFile.from_file() validates the CRC with check_crc=True hardcoded and
    # raises on a mismatch. Real Garmin files (re-exports, multi-session, etc.)
    # often trip this even though the data is intact, so read the bytes and
    # parse with check_crc=False — fit-tool then warns instead of aborting.
    with open(path, "rb") as fh:
        fit = FitFile.from_bytes(fh.read(), check_crc=False)

    sport = sub_sport = None
    sess_start = sess_dist = sess_elapsed = sess_avg_hr = None
    laps = []
    records = []  # (t_epoch, hr, speed_m_s)
    workout_name = None
    workout_steps = []

    for rec in fit.records:
        msg = rec.message
        if isinstance(msg, SessionMessage):
            sport = normalize_enum(_get(msg, "sport"), Sport)
            sub_sport = normalize_enum(_get(msg, "sub_sport"), SubSport)
            sess_start = to_epoch_seconds(_get(msg, "start_time"))
            sess_dist = _get(msg, "total_distance")
            sess_elapsed = _get(msg, "total_elapsed_time")
            sess_avg_hr = _get(msg, "avg_heart_rate")
        elif WorkoutMessage is not None and isinstance(msg, WorkoutMessage):
            workout_name = _get(msg, "wkt_name", "workout_name")
        elif WorkoutStepMessage is not None and isinstance(msg, WorkoutStepMessage):
            workout_steps.append(_read_workout_step(msg))
        elif isinstance(msg, LapMessage):
            laps.append({
                "distance_m": _get(msg, "total_distance") or 0.0,
                "duration_s": _get(msg, "total_elapsed_time", "total_timer_time") or 0.0,
                "hr_avg": _get(msg, "avg_heart_rate"),
                "max_hr": _get(msg, "max_heart_rate"),
                "lap_trigger": normalize_enum(_get(msg, "lap_trigger"), LapTrigger),
                # Garmin tags every recorded lap with its role when the run
                # followed a structured workout: warmup / active / rest /
                # cooldown / recovery / interval. This is the device's own
                # ground truth — far more reliable than guessing from pace.
                "intensity": normalize_enum(_get(msg, "intensity"), Intensity),
                "start_time": to_epoch_seconds(_get(msg, "start_time")),
            })
        elif isinstance(msg, RecordMessage):
            t = to_epoch_seconds(_get(msg, "timestamp"))
            hr = _get(msg, "heart_rate")
            speed = _get(msg, "enhanced_speed", "speed")
            records.append((t, hr, speed))

    # --- running detection ------------------------------------------------
    is_running = (sport in RUNNING_TOKENS or sub_sport in RUNNING_TOKENS
                  or "running" in (sport or "") or "running" in (sub_sport or ""))
    if not is_running:
        # Accept generic/empty sport if laps look like running paces.
        if sport in ("", "generic", None):
            plausible = []
            for lp in laps:
                p = pace_sec_km(lp["distance_m"], lp["duration_s"])
                if p is not None:
                    plausible.append(180 <= p <= 480)  # 3:00–8:00 /km
            if plausible and sum(plausible) / len(plausible) >= 0.5:
                is_running = True
        if not is_running:
            return None, f"not a running activity (sport={sport or 'unknown'})"

    if not laps:
        return None, "no lap data in file"

    # --- merge stub laps (final stop-press laps) -------------------------
    laps = _merge_stub_laps(laps)
    if not laps:
        return None, "no usable laps after cleaning"

    # --- fill missing session totals from laps ---------------------------
    if not sess_start:
        sess_start = laps[0]["start_time"]
    if not sess_dist:
        sess_dist = sum(lp["distance_m"] for lp in laps)
    if not sess_elapsed:
        sess_elapsed = sum(lp["duration_s"] for lp in laps)

    # --- lap start offsets (seconds from session start) ------------------
    cum = 0.0
    for lp in laps:
        if lp["start_time"] is not None and sess_start is not None:
            lp["start_offset_s"] = max(0.0, lp["start_time"] - sess_start)
        else:
            lp["start_offset_s"] = cum
        cum = lp["start_offset_s"] + lp["duration_s"]
        lp.pop("start_time", None)

    # --- HR trace (seconds from start) -----------------------------------
    t0 = None
    for t, _, _ in records:
        if t is not None:
            t0 = t
            break
    if t0 is None and sess_start is not None:
        t0 = sess_start

    trace = []          # full-res {t, hr}
    paced = []          # (t, hr, pace_sec_km) for HR@RefPace
    if t0 is not None:
        for t, hr, speed in records:
            if t is None or hr is None:
                continue
            rel = int(round(t - t0))
            if rel < 0:
                continue
            trace.append({"t": rel, "hr": int(hr)})
            if speed and speed > 0:
                paced.append((rel, int(hr), 1000.0 / speed))

    # --- flag-independent scalars from the trace -------------------------
    hr_at_ref, ref_secs = _hr_at_reference_pace(paced)
    zone_seconds, below_z2, trace_secs = _zone_breakdown(trace)

    # --- identity --------------------------------------------------------
    stem = os.path.splitext(os.path.basename(path))[0]
    start_dt = datetime.fromtimestamp(sess_start, tz=timezone.utc) if sess_start else datetime.now(timezone.utc)
    date_iso = start_dt.strftime("%Y-%m-%d")
    date_fmt = f"{start_dt.day} {MONTHS[start_dt.month - 1]} {start_dt.year}"

    # The Garmin activity title is not stored inside an exported FIT; garmin_sync
    # writes it to a "<stem>.meta.json" sidecar so a naming convention in the
    # title can drive interval detection on synced files too.
    activity_name = _read_activity_name_sidecar(path)

    # Best-effort structure planned in a Garmin structured workout (intensity
    # markers tell us warmup/cooldown/recovery/active without pace guessing).
    planned = _extract_planned_from_steps(workout_steps)

    record = {
        "id": stem,
        "date_iso": date_iso,
        "date": date_fmt,
        "sport": sport or sub_sport or "running",
        "total_distance_m": round(sess_dist, 1),
        "total_elapsed_s": round(sess_elapsed, 1),
        "avg_hr": int(sess_avg_hr) if sess_avg_hr else None,
        "laps": laps,
        "trace": trace,
        "hr_at_ref_pace": hr_at_ref,
        "ref_in_band_seconds": ref_secs,
        "zone_seconds": zone_seconds,
        "seconds_below_zone2": below_z2,
        "trace_seconds": trace_secs,
        # detection inputs (read-only signals from the file/garmin)
        "workout_name": workout_name or None,
        "activity_name": activity_name,
        "planned": planned,
        # user-editable flags / overrides (defaults)
        "easy": False,
        "track": ("track" in (sub_sport or "")) or (sub_sport == "track_running"),
        "theoretical_target": None,
        "structure": None,     # user-typed "5x5'" / "5x4km p1'" override
        "lap_types": {},        # {lap_index: "wu"|"cd"|"active"|"recovery"|"drill"}
    }
    return record, None


def _read_workout_step(msg):
    """Normalise one WorkoutStepMessage into a plain dict (defensive)."""
    dtype = normalize_enum(_get(msg, "duration_type"), WorkoutStepDuration)
    intensity = normalize_enum(_get(msg, "intensity"), Intensity)
    return {
        "index": _get(msg, "message_index"),
        "name": _get(msg, "wkt_step_name"),
        "dtype": dtype,
        "intensity": intensity,
        # fit-tool exposes scaled accessors when available: seconds / metres.
        "seconds": _get(msg, "duration_time"),
        "meters": _get(msg, "duration_distance"),
        # repeat steps point back to an earlier step index and carry a count.
        "repeat_from": _get(msg, "duration_step", "duration_value"),
        "repeat_count": _get(msg, "repeat_steps", "target_value"),
    }


def _read_activity_name_sidecar(fit_path):
    """Return the activity title from a "<stem>.meta.json" sidecar, if present."""
    side = os.path.splitext(fit_path)[0] + ".meta.json"
    if not os.path.exists(side):
        return None
    try:
        with open(side, "r", encoding="utf-8") as fh:
            return (json.load(fh) or {}).get("activity_name") or None
    except (OSError, json.JSONDecodeError):
        return None


def _extract_planned_from_steps(steps):
    """Derive an interval spec from structured-workout steps.

    Returns {"itype","rep_count","rep_target","recovery_target","source"} or
    None. Repeats are expanded; intensity markers split active from rest. This
    is best-effort: any uncertainty returns None so name/heuristic take over.
    """
    if not steps:
        return None
    ordered = sorted(steps, key=lambda s: (s["index"] if s["index"] is not None else 0))
    flat = []
    guard = 0
    for s in ordered:
        guard += 1
        if guard > 5000:
            break
        if s["dtype"] == "repeat_until_steps_cmplt":
            frm = s["repeat_from"]
            cnt = int(s["repeat_count"]) if s["repeat_count"] else 1
            if frm is None or cnt <= 1:
                continue
            block = [b for b in ordered if b["index"] is not None
                     and frm <= b["index"] < (s["index"] or 0)
                     and b["dtype"] != "repeat_until_steps_cmplt"]
            for _ in range(cnt - 1):  # block already appears once inline
                flat.extend(block)
        else:
            flat.append(s)

    def is_active(st):
        return st["intensity"] in ("active", "interval", "")
    actives = [s for s in flat if is_active(s) and s["dtype"] in ("time", "distance")]
    if not actives:
        return None
    itype = "time" if actives[0]["dtype"] == "time" else "distance"
    measures = [(a["seconds"] if itype == "time" else a["meters"]) for a in actives]
    measures = [m for m in measures if m]
    if not measures:
        return None
    rest = [s for s in flat if s["intensity"] in ("rest", "recovery")]
    # Rest steps carry their own duration type (e.g. 90 s rest between 400 m reps).
    rec_itype = None
    if rest:
        rec_itype = "time" if rest[0]["dtype"] == "time" else "distance"
    rec_vals = [(r["seconds"] if rec_itype == "time" else r["meters"]) for r in rest]
    rec_vals = [v for v in rec_vals if v]
    return {
        "itype": itype,
        "rep_count": len(actives),
        "rep_target": statistics.median(measures),
        "recovery_target": statistics.median(rec_vals) if rec_vals else None,
        "recovery_itype": rec_itype,
        "source": "workout",
    }


def _merge_stub_laps(laps):
    """Drop/merge final stub laps (< 10 s or < 30 m) into the previous lap."""
    cleaned = []
    for lp in laps:
        is_stub = (lp["duration_s"] < 10) or (lp["distance_m"] < 30)
        if is_stub and cleaned:
            prev = cleaned[-1]
            prev["distance_m"] += lp["distance_m"]
            prev["duration_s"] += lp["duration_s"]
        elif is_stub and not cleaned:
            continue  # leading stub — drop
        else:
            cleaned.append(lp)
    return cleaned


def _hr_at_reference_pace(paced):
    """Mean HR over trace seconds whose pace falls in REFERENCE_PACE_BAND.

    Requires >= 3 min in-band, else returns (None, seconds_in_band).
    """
    lo, hi = config.REFERENCE_PACE_BAND
    hrs = [hr for (_t, hr, pace) in paced if lo <= pace <= hi]
    if len(hrs) >= 180:
        return round(statistics.mean(hrs), 1), len(hrs)
    return None, len(hrs)


def _zone_breakdown(trace):
    """Return (zone_seconds[5], seconds_below_zone2, total_seconds).

    Zones from HR_ZONES fractions of hr_max. Assumes ~1 Hz trace.
    """
    hr_max = config.USER_PROFILE["hr_max"]
    # HR_ZONES holds 6 fractional boundaries → 5 zones (consecutive pairs).
    bounds = [f * hr_max for f in config.HR_ZONES]
    uppers = bounds[1:]              # 5 upper bounds, one per zone
    zone_seconds = [0] * len(uppers)
    below_z2 = 0
    z2_ceiling = config.USER_PROFILE["zone2_hr"][1]
    for pt in trace:
        hr = pt["hr"]
        if hr < z2_ceiling:
            below_z2 += 1
        placed = False
        for i, ub in enumerate(uppers):
            if hr < ub:
                zone_seconds[i] += 1
                placed = True
                break
        if not placed:
            zone_seconds[-1] += 1
    return zone_seconds, below_z2, len(trace)


# ════════════════════════════════════════════════════════════════════
# Structure resolution (what was the planned set?)
# ════════════════════════════════════════════════════════════════════
import re

_DUR_RE = re.compile(
    r"(?P<n>\d+)\s*[x×]\s*"                       # rep count, e.g. 5x
    r"(?P<val>\d+(?:[.:]\d+)?)\s*"                # value, e.g. 5 / 1.5 / 1:30
    r"(?P<unit>'|\"|min(?:ute)?s?|sec(?:ond)?s?|s|m\b|km|k|metri|metres?|meters?)?",
    re.IGNORECASE)
_REC_RE = re.compile(
    r"(?:p|r|rec(?:upero|overy)?|rest)\s*"        # recovery marker p / r / rec
    r"(?P<val>\d+(?:[.:]\d+)?)\s*"
    r"(?P<unit>'|\"|min(?:ute)?s?|sec(?:ond)?s?|s|m\b|km|k|metri|metres?|meters?)?",
    re.IGNORECASE)


def _value_to_seconds_or_meters(val, unit):
    """Parse '5'/'90/'1:30'/'4km'/'400m' → (kind, amount) where kind is
    'time' (seconds) or 'distance' (metres). Returns (None, None) if unclear."""
    unit = (unit or "").lower().strip()
    # mm:ss always means time
    if ":" in val:
        mm, ss = val.split(":", 1)
        return "time", int(mm) * 60 + int(ss)
    num = float(val)
    if unit in ("'", "min", "mins", "minute", "minutes"):
        return "time", num * 60
    if unit in ('"', "s", "sec", "secs", "second", "seconds"):
        return "time", num
    if unit in ("km", "k"):
        return "distance", num * 1000
    if unit in ("m", "metri", "metre", "metres", "meter", "meters"):
        return "distance", num
    # No explicit unit: disambiguate by magnitude. ≤ 60 → minutes, else metres.
    if num <= 60:
        return "time", num * 60
    return "distance", num


def parse_structure_string(text):
    """Parse a naming-convention string like '5x5'', '10x90\"', '5x4km p1''.

    Returns a spec dict or None. Tolerant of warmup/cooldown words around it.
    """
    if not text:
        return None
    m = _DUR_RE.search(text)
    if not m:
        return None
    kind, amount = _value_to_seconds_or_meters(m.group("val"), m.group("unit"))
    if kind is None:
        return None
    spec = {
        "itype": kind,
        "rep_count": int(m.group("n")),
        "rep_target": amount,
        "recovery_target": None,
        "recovery_itype": None,
        "source": "name",
    }
    rm = _REC_RE.search(text[m.end():])
    if rm:
        rkind, ramount = _value_to_seconds_or_meters(rm.group("val"), rm.group("unit"))
        if rkind is not None:
            spec["recovery_target"] = ramount
            spec["recovery_itype"] = rkind
    return spec


def resolve_structure(record):
    """Combine all structure signals, most authoritative first.

    Order: user-typed structure → embedded workout → activity/workout name →
    filename. Returns (spec_or_None, source_label).
    """
    # 1. explicit user override
    spec = parse_structure_string(record.get("structure"))
    if spec:
        spec["source"] = "manual"
        return spec, "manual"
    # 2. embedded structured workout
    planned = record.get("planned")
    if planned and planned.get("rep_count"):
        return planned, "workout"
    # 3. a naming convention in the title / workout name / filename
    for field in ("activity_name", "workout_name", "id"):
        spec = parse_structure_string(record.get(field))
        if spec:
            spec["source"] = "name"
            return spec, "name"
    return None, "heuristic"


# ════════════════════════════════════════════════════════════════════
# Interval detection
# ════════════════════════════════════════════════════════════════════
def detect_intervals(laps, track, spec=None, lap_types=None):
    """Classify laps into wu / cd / active / recovery / drill and infer type.

    Layered, most reliable signal first:
      1. explicit per-lap overrides (lap_types) always win;
      2. a structure the user typed by hand (manual spec) — explicit intent;
      3. Garmin's own per-lap intensity markers recorded in the FIT
         (warmup / active / rest / cooldown …) — the device ground truth,
         so warm-up, cool-down and drills are tagged at the source, not guessed;
      4. a structure inferred from a workout plan or the activity title, matched
         to the planned rep target;
      5. otherwise the legacy pace-ratio heuristic.

    Returns (classified_laps, itype) where itype is 'distance' | 'time' | None.
    classified_laps mirrors `laps` with an added 'type' (+ 'rep' for actives).
    Easy runs never reach here.
    """
    n = len(laps)
    out = [dict(lp) for lp in laps]
    lap_types = {int(k): v for k, v in (lap_types or {}).items()
                 if str(v) in ("wu", "cd", "active", "recovery", "drill")}

    manual_spec = bool(spec and spec.get("source") == "manual" and spec.get("rep_count"))
    if manual_spec:
        _classify_with_spec(out, spec)
    elif _has_meaningful_lap_intensity(out):
        _classify_with_lap_intensity(out)
    elif spec and spec.get("rep_count"):
        _classify_with_spec(out, spec)
    else:
        _classify_heuristic(out)

    # Per-lap overrides win over everything.
    for i, t in lap_types.items():
        if 0 <= i < n:
            out[i]["type"] = t

    # Number the active reps in order.
    rep = 0
    for lp in out:
        if lp["type"] == "active":
            rep += 1
            lp["rep"] = rep
        else:
            lp.pop("rep", None)

    itype = (spec.get("itype") if spec and spec.get("itype")
             else _classify_interval_type(out))
    return out, itype


# Garmin lap-intensity tokens → our lap roles.
_INTENSITY_TO_TYPE = {
    "warmup": "wu",
    "cooldown": "cd",
    "rest": "recovery",
    "recovery": "recovery",
    "active": "active",
    "interval": "active",
}


def _has_meaningful_lap_intensity(laps):
    """True when the FIT carries usable per-lap intensity markers.

    A plain free run leaves every lap 'active' (or blank), which tells us
    nothing. We only trust this signal when the laps actually distinguish work
    from rest/warm-up — i.e. at least one active lap AND at least one
    warm-up / cool-down / rest lap.
    """
    vals = [lp.get("intensity") for lp in laps]
    vals = [v for v in vals if v in _INTENSITY_TO_TYPE]
    if len(vals) < 2:
        return False
    has_active = any(_INTENSITY_TO_TYPE[v] == "active" for v in vals)
    has_other = any(_INTENSITY_TO_TYPE[v] in ("wu", "cd", "recovery") for v in vals)
    return has_active and has_other


def _classify_with_lap_intensity(out):
    """Classify straight from Garmin's recorded per-lap intensity markers.

    No pace guessing: the watch already tagged each lap when the run followed a
    structured workout. Laps with an unknown/blank marker are filled by their
    position — before the first active lap they are warm-up, after the last they
    are cool-down, in between they are recovery.
    """
    for lp in out:
        lp["type"] = _INTENSITY_TO_TYPE.get(lp.get("intensity"))

    active_idx = [i for i, lp in enumerate(out) if lp["type"] == "active"]
    if not active_idx:
        _classify_heuristic(out)
        return

    first, last = active_idx[0], active_idx[-1]
    for i, lp in enumerate(out):
        if lp["type"]:
            continue
        if i < first:
            lp["type"] = "wu"
        elif i > last:
            lp["type"] = "cd"
        else:
            lp["type"] = "recovery"


def _lap_measure(lp, itype):
    return lp["duration_s"] if itype == "time" else lp["distance_m"]


def _classify_with_spec(out, spec):
    """Match laps to a known planned set: the laps closest to the rep target
    (and there should be rep_count of them) are the active reps; laps between
    them are recovery; leading/trailing laps are warm-up / cool-down. Anything
    near the start that does not match the target (e.g. drills, strides) stays
    warm-up rather than being miscounted as a rep."""
    itype = spec["itype"]
    target = spec.get("rep_target")
    count = spec.get("rep_count")

    for lp in out:
        lp["type"] = "wu"  # provisional; promoted below

    # Score every lap by closeness to the target measure.
    if target:
        tol = 0.30  # ±30% — separates 400s from 800s, reps from recoveries/drills
        matched = [i for i, lp in enumerate(out)
                   if _lap_measure(lp, itype) and
                   abs(_lap_measure(lp, itype) - target) <= tol * target]
        if count and len(matched) > count:
            # More laps match the target than there are planned reps — happens
            # when recoveries or drills share the rep distance (e.g. 200 m hard
            # / 200 m float). The reps are the faster efforts, so keep the
            # fastest `count` matched laps as the actives.
            matched.sort(key=lambda i: (pace_sec_km(out[i]["distance_m"],
                                                    out[i]["duration_s"]) or 1e9))
            active_idx = sorted(matched[:count])
        else:
            active_idx = sorted(matched)
    else:
        # No target: take the `count` fastest laps as the reps.
        paces = [(pace_sec_km(lp["distance_m"], lp["duration_s"]), i)
                 for i, lp in enumerate(out)]
        paces = [(p, i) for p, i in paces if p is not None]
        paces.sort()
        active_idx = sorted(i for _, i in paces[:count]) if count else []

    if not active_idx:
        _classify_heuristic(out)
        return

    first, last = active_idx[0], active_idx[-1]
    active_set = set(active_idx)
    for i, lp in enumerate(out):
        if i in active_set:
            lp["type"] = "active"
        elif i < first:
            lp["type"] = "wu"
        elif i > last:
            lp["type"] = "cd"
        else:
            lp["type"] = "recovery"


def _classify_heuristic(out):
    """Legacy pace-ratio fallback when no structure is known."""
    n = len(out)
    for lp in out:
        lp["type"] = "active"
    paces = [pace_sec_km(lp["distance_m"], lp["duration_s"]) for lp in out]
    valid = [p for p in paces if p is not None]
    if not valid:
        return
    mean_pace = statistics.mean(valid)
    factor = config.DETECTION["active_pace_factor"]

    # Step 1 — warm-up / cool-down: slow laps at the extremities.
    threshold = mean_pace * factor
    first_fast = 0
    while first_fast < n and (paces[first_fast] is None or paces[first_fast] > threshold):
        out[first_fast]["type"] = "wu"
        first_fast += 1
    last_fast = n - 1
    while last_fast > first_fast and (paces[last_fast] is None or paces[last_fast] > threshold):
        out[last_fast]["type"] = "cd"
        last_fast -= 1

    # Step 2 — active vs recovery inside the window.
    rec_factor = config.DETECTION["recovery_pace_factor"]
    window = list(range(first_fast, last_fast + 1))
    i = 0
    while i < len(window):
        idx = window[i]
        p = paces[idx]
        nxt = window[i + 1] if i + 1 < len(window) else None
        if p is None:
            out[idx]["type"] = "recovery"
            i += 1
            continue
        if nxt is not None and paces[nxt] is not None and paces[nxt] >= p * rec_factor:
            out[idx]["type"] = "active"
            out[nxt]["type"] = "recovery"
            i += 2
        else:
            out[idx]["type"] = "active" if p <= mean_pace else "recovery"
            i += 1


def _classify_interval_type(classified):
    """time-based vs distance-based from lap_trigger, with a CV fallback."""
    actives = [lp for lp in classified if lp["type"] == "active"]
    if not actives:
        return None
    triggers = [lp.get("lap_trigger", "") for lp in actives]
    time_votes = sum(1 for t in triggers if t == "time")
    dist_votes = sum(1 for t in triggers if t in ("distance", "manual"))
    if time_votes or dist_votes:
        return "time" if time_votes > dist_votes else "distance"

    # Fallback: coefficient of variation.
    durs = [lp["duration_s"] for lp in actives]
    dists = [lp["distance_m"] for lp in actives]
    cv_dur = _cv(durs)
    cv_dist = _cv(dists)
    if cv_dur is not None and cv_dist is not None:
        if cv_dur < 5 and cv_dist > 10:
            return "time"
        if cv_dist < 5 and cv_dur > 10:
            return "distance"
    return "distance"  # ambiguous → distance + UI warning


def _cv(values):
    vals = [v for v in values if v]
    if len(vals) < 2:
        return None
    m = statistics.mean(vals)
    if m == 0:
        return None
    return statistics.pstdev(vals) / m * 100.0


# ════════════════════════════════════════════════════════════════════
# Distance rounding
# ════════════════════════════════════════════════════════════════════
def round_distance(distance_m, track):
    """Snap a distance-based rep to a standard, or return raw if no match."""
    if distance_m > config.DETECTION["rounding_max_m"]:
        return round(distance_m), False
    for std, t_lo, t_hi, r_lo, r_hi in config.ROUNDING_TABLE:
        lo, hi = (t_lo, t_hi) if track else (r_lo, r_hi)
        if lo <= distance_m <= hi:
            return std, True
    return round(distance_m), False


# ════════════════════════════════════════════════════════════════════
# Score functions (piecewise linear / banded)
# ════════════════════════════════════════════════════════════════════
def _lerp(x, points):
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return y0
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def pace_score(delta_pct, time_based=False):
    """Δ 0%→100, +3%→70, −3%→85, |Δ|≥10%→0. Sign inverted for time-based."""
    if delta_pct is None:
        return None
    d = -delta_pct if time_based else delta_pct
    return _lerp(d, [(-10, 0), (-3, 85), (0, 100), (3, 70), (10, 0)])


def ef_score(value, median):
    """EA normalised vs same-label median: ±5%→75, +10%→100, −10%→40."""
    if value is None or not median:
        return None
    dev = (value / median - 1.0) * 100.0
    return _lerp(dev, [(-10, 40), (-5, 75), (5, 75), (10, 100)])


def decoupling_score(dec_pct):
    if dec_pct is None:
        return None
    a = abs(dec_pct)
    if a < 3:
        return 100
    if a < 5:
        return 80
    if a < 8:
        return 60
    return 20


def fade_score(fade_pct):
    """Pace-fade slot for interval SPS. Negative fade (speeding up) = good."""
    if fade_pct is None:
        return None
    if fade_pct < 0:
        return 100
    if fade_pct < 2:
        return 80
    if fade_pct < 5:
        return 60
    return 20


def recovery_score(hrr60=None, rqs=None):
    if hrr60 is not None:
        if hrr60 > 30:
            return 100
        if hrr60 >= 25:
            return 85
        if hrr60 >= 15:
            return 60
        return 30
    if rqs is not None:
        if rqs < 70:
            return 100
        if rqs < 75:
            return 80
        if rqs < 85:
            return 50
        return 20
    return None


def weighted_score(components):
    """components: {key: (weight, score|None)}. Drop None, renormalise."""
    present = {k: (w, s) for k, (w, s) in components.items() if s is not None}
    total_w = sum(w for w, _ in present.values())
    if total_w == 0:
        return None, []
    val = sum(w * s for w, s in present.values()) / total_w
    return round(val, 1), list(present.keys())


# ════════════════════════════════════════════════════════════════════
# Trace lookups
# ════════════════════════════════════════════════════════════════════
def hr_at_time(trace, t, tol=8):
    """Nearest HR sample to time t (seconds), within tolerance."""
    best = None
    best_d = tol + 1
    for pt in trace:
        d = abs(pt["t"] - t)
        if d < best_d:
            best_d = d
            best = pt["hr"]
            if d == 0:
                break
    return best


# ════════════════════════════════════════════════════════════════════
# Session view builder — derives the public API shape from a stored record
# ════════════════════════════════════════════════════════════════════
def build_session_view(record, store):
    easy = bool(record.get("easy"))
    track = bool(record.get("track"))
    trace = record.get("trace", [])
    theoretical_target = record.get("theoretical_target")

    km = round(record["total_distance_m"] / 1000.0, 2)
    view = {
        "id": record["id"],
        "date": record["date"],
        "date_iso": record["date_iso"],
        "km": km,
        "duration_fmt": fmt_duration(record["total_elapsed_s"]),
        "duration_s": record["total_elapsed_s"],
        "avg_hr": record["avg_hr"],
        "easy": easy,
        "track": track,
        "theoretical_target": theoretical_target,
        "hr_at_ref_pace": record.get("hr_at_ref_pace"),
        "ef": None, "decoupling": None,
        "pace_fade": None, "pace_cv": None,
        "rqs_avg": None, "hrr60_avg": None,
        "sps_t": None, "sps_i": None,
        "recovery_target_fmt": None, "recovery_actual_fmt": None,
        "recovery_adherence": None,
        "inferred_target": None,
        "itype": None, "label": None,
        "structure": record.get("structure"),
        "structure_source": "heuristic",
        "activity_name": record.get("activity_name"),
        "workout_name": record.get("workout_name"),
        "warnings": [],
        "laps": [],
        "hr_trace": _subsample(trace, config.DETECTION["hr_trace_max_points"]),
    }

    # Session EF (KPI-01) — always computable with HR.
    view["ef"] = efficiency_factor(record["total_distance_m"],
                                   record["total_elapsed_s"], record["avg_hr"])

    if easy:
        # Easy / steady: EF + Decoupling + HR@RefPace only; no per-lap KPIs.
        view["label"] = "Easy run"
        view["decoupling"] = compute_decoupling(record["laps"])
        return view

    # --- interval pipeline ----------------------------------------------
    spec, source = resolve_structure(record)
    # When no explicit/inferred structure applies but the FIT carries Garmin's
    # own per-lap intensity markers, that is what actually drives detection.
    if source == "heuristic" and _has_meaningful_lap_intensity(record["laps"]):
        source = "garmin"
    view["structure"] = record.get("structure")
    view["structure_source"] = source       # manual | workout | name | garmin | heuristic
    view["activity_name"] = record.get("activity_name")
    view["workout_name"] = record.get("workout_name")
    classified, itype = detect_intervals(record["laps"], track, spec,
                                         record.get("lap_types"))
    view["itype"] = itype
    actives = [lp for lp in classified if lp["type"] == "active"]

    # historical same-label EA median for EF/EA scoring
    label_guess = None  # computed below; for scoring we use itype + count later
    ea_median = _ea_history_median(record, store)

    # per-lap build
    lap_views = []
    rep_paces = []
    rep_durations = []
    rqs_values = []
    hrr_values = []
    inferred_pace = None
    inferred_dur = None
    if actives:
        ap = [pace_sec_km(lp["distance_m"], lp["duration_s"]) for lp in actives]
        ap = [p for p in ap if p is not None]
        inferred_pace = statistics.median(ap) if ap else None
        inferred_dur = statistics.median([lp["duration_s"] for lp in actives])

    for i, lp in enumerate(classified):
        lv = {"type": lp["type"], "distance_m": round(lp["distance_m"], 1),
              "duration_s": round(lp["duration_s"], 1),
              "hr_avg": int(lp["hr_avg"]) if lp.get("hr_avg") else None,
              "pace_fmt": fmt_pace(pace_sec_km(lp["distance_m"], lp["duration_s"]))}

        if lp["type"] == "active":
            p = pace_sec_km(lp["distance_m"], lp["duration_s"])
            lv["rep"] = lp["rep"]
            rep_paces.append(p)
            rep_durations.append(lp["duration_s"])

            # rounded distance for distance-based reps
            if itype == "distance":
                std, matched = round_distance(lp["distance_m"], track)
                lv["distance_std"] = std
            else:
                lv["distance_std"] = None

            # EA per lap (LAP-03)
            lv["ea"] = round(p / lp["hr_avg"], 3) if (p and lp.get("hr_avg")) else None

            # Cardiac cost (LAP-05): active HR − preceding lap HR
            prev_hr = classified[i - 1]["hr_avg"] if i > 0 else None
            lv["cardiac_cost"] = (int(lp["hr_avg"] - prev_hr)
                                  if (lp.get("hr_avg") and prev_hr) else None)

            # HRR60 (LAP-04) from trace, else RQS (LAP-04b)
            hrr60 = None
            rqs = None
            end_t = lp.get("start_offset_s", 0) + lp["duration_s"]
            if trace:
                hr_end = hr_at_time(trace, end_t)
                hr_later = hr_at_time(trace, end_t + 60)
                if hr_end is not None and hr_later is not None:
                    hrr60 = int(hr_end - hr_later)
            nxt = classified[i + 1] if i + 1 < len(classified) else None
            if nxt and nxt["type"] == "recovery" and nxt.get("hr_avg") and lp.get("hr_avg"):
                rqs = round(nxt["hr_avg"] / lp["hr_avg"] * 100.0, 1)
            lv["hrr60"] = hrr60
            lv["rqs"] = rqs
            if hrr60 is not None:
                hrr_values.append(hrr60)
            if rqs is not None:
                rqs_values.append(rqs)

            # Δ vs theoretical / inferred (LAP-06 / LAP-07)
            tgt = _parse_target(theoretical_target, itype)
            if itype == "time":
                base = lp["duration_s"]
                lv["delta_t"] = _pct(base, tgt) if tgt else None
                lv["delta_i"] = _pct(base, inferred_dur) if inferred_dur else None
            else:
                lv["delta_t"] = _pct(p, tgt) if tgt else None
                lv["delta_i"] = _pct(p, inferred_pace) if inferred_pace else None

            # Lap score (LAP-08): pace, EA-vs-median, recovery
            ps = pace_score(_pct_num(p if itype != "time" else lp["duration_s"],
                                     inferred_pace if itype != "time" else inferred_dur),
                            time_based=(itype == "time"))
            es = ef_score(lv["ea"], ea_median)
            rs = recovery_score(hrr60=hrr60, rqs=rqs)
            w = config.LAP_SCORE_WEIGHTS
            lscore, _ = weighted_score({
                "pace": (w["pace"], ps),
                "ef": (w["ef"], es),
                "recovery": (w["recovery"], rs),
            })
            lv["lap_score"] = lscore

        elif lp["type"] == "recovery":
            # recovery laps: show distance/duration/HR + RQS/HRR60 of the
            # preceding active are attached to the active row instead.
            prev = classified[i - 1] if i > 0 else None
            if prev and prev.get("hr_avg") and lp.get("hr_avg"):
                lv["rqs"] = round(lp["hr_avg"] / prev["hr_avg"] * 100.0, 1)
            else:
                lv["rqs"] = None
        # wu / cd rows carry just the basics
        lap_views.append(lv)

    view["laps"] = lap_views

    # --- session label ---------------------------------------------------
    view["label"] = _build_label(actives, itype, track, spec)

    # --- session KPIs ----------------------------------------------------
    if rep_paces and len([p for p in rep_paces if p]) >= 2:
        clean = [p for p in rep_paces if p]
        view["pace_fade"] = round((clean[-1] - clean[0]) / clean[0] * 100.0, 1)
        view["pace_cv"] = round(statistics.pstdev(clean) / statistics.mean(clean) * 100.0, 1)
    if rqs_values:
        view["rqs_avg"] = round(statistics.mean(rqs_values), 1)
    if hrr_values:
        view["hrr60_avg"] = round(statistics.mean(hrr_values), 1)

    # inferred target display
    if inferred_pace and itype != "time":
        view["inferred_target"] = fmt_pace(inferred_pace)
    elif inferred_dur and itype == "time":
        view["inferred_target"] = fmt_duration(inferred_dur)

    # recovery adherence — did the actual recoveries match the planned rest?
    _attach_recovery_adherence(view, classified, spec)

    # SPS-I (inferred) and SPS-T (theoretical, null until target set)
    view["sps_i"] = _compute_sps(view, rep_paces, rep_durations, hrr_values,
                                 rqs_values, ea_median, itype,
                                 inferred_pace, inferred_dur, target=None)
    tgt = _parse_target(theoretical_target, itype)
    if tgt:
        view["sps_t"] = _compute_sps(view, rep_paces, rep_durations, hrr_values,
                                     rqs_values, ea_median, itype,
                                     inferred_pace, inferred_dur, target=tgt)

    # interval-type ambiguity warning
    actives_triggers = [lp.get("lap_trigger") for lp in record["laps"]]
    if itype and not any(t in ("time", "distance", "manual") for t in actives_triggers):
        view["warnings"].append("Interval type could not be determined — please verify")

    return view


def efficiency_factor(distance_m, duration_s, hr_avg):
    """KPI-01: speed (m/min) / HR. Higher = better."""
    if not hr_avg or not duration_s or not distance_m:
        return None
    speed_m_per_min = distance_m / (duration_s / 60.0)
    return round(speed_m_per_min / hr_avg, 3)


def compute_decoupling(laps):
    """KPI-02: (EF_first_half − EF_second_half) / EF_first_half × 100.

    Steady sessions only. Split by cumulative duration; the straddling lap is
    divided proportionally. HR per half is duration-weighted lap HR.
    """
    usable = [lp for lp in laps if lp["duration_s"] > 0 and lp["distance_m"] > 0]
    if not usable or any(lp.get("hr_avg") is None for lp in usable):
        return None
    total_dur = sum(lp["duration_s"] for lp in usable)
    if total_dur <= 0:
        return None
    half = total_dur / 2.0

    halves = [{"dist": 0.0, "dur": 0.0, "hr_dur": 0.0}, {"dist": 0.0, "dur": 0.0, "hr_dur": 0.0}]
    cum = 0.0
    for lp in usable:
        d = lp["duration_s"]
        start = cum
        end = cum + d
        # portion in first half
        first_part = max(0.0, min(end, half) - start)
        second_part = d - first_part
        for part, h in ((first_part, halves[0]), (second_part, halves[1])):
            if part <= 0:
                continue
            frac = part / d
            h["dist"] += lp["distance_m"] * frac
            h["dur"] += part
            h["hr_dur"] += lp["hr_avg"] * part
        cum = end

    efs = []
    for h in halves:
        if h["dur"] <= 0 or h["hr_dur"] <= 0:
            return None
        hr = h["hr_dur"] / h["dur"]
        speed = h["dist"] / (h["dur"] / 60.0)
        efs.append(speed / hr)
    if efs[0] == 0:
        return None
    return round((efs[0] - efs[1]) / efs[0] * 100.0, 1)


def _compute_sps(view, rep_paces, rep_durations, hrr_values, rqs_values,
                 ea_median, itype, inferred_pace, inferred_dur, target):
    """SPS-T / SPS-I per SPS_WEIGHTS {pace, ef, fade_or_decoupling, recovery}."""
    # pace component: mean pace score of reps vs (target or inferred)
    if itype == "time":
        ref = target if target else inferred_dur
        deltas = [_pct_num(d, ref) for d in rep_durations if d]
        pscores = [pace_score(x, time_based=True) for x in deltas if x is not None]
    else:
        ref = target if target else inferred_pace
        deltas = [_pct_num(p, ref) for p in rep_paces if p]
        pscores = [pace_score(x) for x in deltas if x is not None]
    pace_comp = statistics.mean(pscores) if pscores else None

    # ef component: session EA vs same-label median
    sess_ea = None
    if view["avg_hr"] and view["duration_s"] and view["km"]:
        sess_ea = (view["duration_s"] / view["km"]) / view["avg_hr"]
    ef_comp = ef_score(sess_ea, ea_median)

    # fade/decoupling component (interval → fade)
    fade_comp = fade_score(view["pace_fade"])

    # recovery component
    rec_comp = recovery_score(
        hrr60=(statistics.mean(hrr_values) if hrr_values else None),
        rqs=(statistics.mean(rqs_values) if rqs_values else None),
    )

    w = config.SPS_WEIGHTS
    score, _ = weighted_score({
        "pace": (w["pace"], pace_comp),
        "ef": (w["ef"], ef_comp),
        "fade_or_decoupling": (w["fade_or_decoupling"], fade_comp),
        "recovery": (w["recovery"], rec_comp),
    })
    return score


def _attach_recovery_adherence(view, classified, spec):
    """Compare the actual recovery laps to the planned rest from the structure.

    Adherence = actual / planned × 100 (100 = on plan, > 100 = over-resting).
    Only computed when a recovery target is known and recovery laps exist.
    """
    if not spec or not spec.get("recovery_target"):
        return
    rtype = spec.get("recovery_itype") or "time"
    target = spec["recovery_target"]
    recs = [lp for lp in classified if lp["type"] == "recovery"]
    actuals = [(lp["duration_s"] if rtype == "time" else lp["distance_m"]) for lp in recs]
    actuals = [a for a in actuals if a]
    if not actuals:
        return
    actual = statistics.mean(actuals)
    fmt = fmt_duration if rtype == "time" else (lambda m: f"{round(m)} m")
    view["recovery_target_fmt"] = fmt(target)
    view["recovery_actual_fmt"] = fmt(actual)
    view["recovery_adherence"] = round(actual / target * 100.0, 0) if target else None


def _build_label(actives, itype, track, spec=None):
    n = len(actives)
    if n == 0:
        return "Run"
    # When the planned structure is known, label from it — exact and clean
    # (e.g. "5×4 km" instead of a rounded "5×4002 m").
    if spec and spec.get("rep_count") and spec.get("rep_target"):
        rc = spec["rep_count"]
        tv = spec["rep_target"]
        if spec["itype"] == "time":
            secs = int(round(tv))
            if secs % 60 == 0:
                return f"{rc}×{secs // 60} min"
            return f"{rc}×{secs} s" if secs < 60 else f"{rc}×{secs // 60}:{secs % 60:02d}"
        m = int(round(tv))
        if m >= 1000 and m % 1000 == 0:
            return f"{rc}×{m // 1000} km"
        return f"{rc}×{m} m"
    if itype == "time":
        mins = [round(lp["duration_s"] / 60.0) for lp in actives]
        if len(set(mins)) == 1:
            return f"{n}×{mins[0]} min"
        return f"{n} reps (mixed)"
    # distance
    stds = []
    for lp in actives:
        std, matched = round_distance(lp["distance_m"], track)
        stds.append(std if matched else round(lp["distance_m"]))
    if len(set(stds)) == 1:
        s = stds[0]
        if s >= 1000 and s % 1000 == 0:
            return f"{n}×{s // 1000} km"
        return f"{n}×{s} m"
    return f"{n} reps (mixed)"


def _ea_history_median(record, store):
    """Median session EA over the last N same-label sessions (EF/EA scoring)."""
    window = config.DETECTION["ea_history_window"]
    eas = []
    for other in store.values():
        if other["id"] == record["id"]:
            continue
        if other.get("easy"):
            continue
        if other.get("date_iso", "") >= record.get("date_iso", ""):
            continue
        km = other["total_distance_m"] / 1000.0
        if other.get("avg_hr") and km > 0:
            eas.append((other["total_elapsed_s"] / km) / other["avg_hr"])
    eas = eas[-window:]
    if len(eas) < 3:
        return None
    return statistics.median(eas)


def _parse_target(value, itype):
    """theoretical_target may be 'mm:ss' (pace or duration) or seconds."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if ":" in s:
        try:
            m, sec = s.split(":")
            return int(m) * 60 + int(sec)
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


def _pct(value, target):
    p = _pct_num(value, target)
    return f"{p:+.1f}%" if p is not None else None


def _pct_num(value, target):
    if not value or not target:
        return None
    return (value - target) / target * 100.0


def _subsample(trace, max_points):
    if len(trace) <= max_points:
        return trace
    step = math.ceil(len(trace) / max_points)
    return trace[::step]


# ════════════════════════════════════════════════════════════════════
# Weekly aggregation
# ════════════════════════════════════════════════════════════════════
def iso_week_start(date_iso):
    d = datetime.strptime(date_iso, "%Y-%m-%d").date()
    return d - timedelta(days=d.weekday())  # Monday


def compute_weekly(store, n_weeks=8):
    views = [build_session_view(r, store) for r in store.values()]
    if not views:
        return {"weeks": []}

    # bucket by Monday
    buckets = {}
    for v, r in zip(views, store.values()):
        wk = iso_week_start(v["date_iso"])
        buckets.setdefault(wk, []).append((v, r))

    all_weeks = sorted(buckets.keys())
    last = all_weeks[-1]
    # build a continuous range of the last n_weeks ending at the latest week
    weeks_range = [last - timedelta(weeks=i) for i in range(n_weeks - 1, -1, -1)]

    out_weeks = []
    prev_km = None
    for wk in weeks_range:
        items = buckets.get(wk, [])
        week = _aggregate_week(wk, items, store)
        # WoW Δ km (WK-07)
        if prev_km is not None and prev_km > 0:
            week["wow_km_pct"] = round((week["total_km"] - prev_km) / prev_km * 100.0, 1)
        else:
            week["wow_km_pct"] = None
        week["wow_alert"] = (week["wow_km_pct"] is not None
                             and week["wow_km_pct"] > config.THRESHOLDS["weekly_increase_alert"])
        prev_km = week["total_km"]
        out_weeks.append(week)

    # ACWR (WK-10) needs rolling 7/28-day TRIMP relative to each week end.
    _attach_acwr(out_weeks, store)
    return {"weeks": out_weeks}


def _aggregate_week(monday, items, store):
    views = [v for v, _ in items]
    recs = [r for _, r in items]
    total_km = round(sum(v["km"] for v in views), 2)
    interval = [v for v in views if not v["easy"]]
    easy = [v for v in views if v["easy"]]

    # WK-03 duration-weighted avg HR
    num = den = 0.0
    for v in views:
        if v["avg_hr"] and v["duration_s"]:
            num += v["avg_hr"] * v["duration_s"]
            den += v["duration_s"]
    avg_hr = round(num / den) if den else None

    # WK-04 weekly EF — easy runs only, distance-weighted
    enum = eden = 0.0
    for v in easy:
        if v["ef"] and v["km"]:
            enum += v["ef"] * v["km"]
            eden += v["km"]
    weekly_ef = round(enum / eden, 3) if eden else None

    # WK-05 interval quality volume (Σ active-lap distance)
    qvol = 0.0
    for v in interval:
        for lp in v["laps"]:
            if lp["type"] == "active":
                qvol += lp["distance_m"]
    qvol = round(qvol / 1000.0, 2)

    # WK-06 weekly SPS avg (interval SPS-T, fallback SPS-I)
    sps_vals = [(v["sps_t"] if v["sps_t"] is not None else v["sps_i"])
                for v in interval]
    sps_vals = [s for s in sps_vals if s is not None]
    weekly_sps = round(statistics.mean(sps_vals), 1) if sps_vals else None

    # WK-08 easy ratio (time-in-zone), fallback km-based
    below = sum(r.get("seconds_below_zone2", 0) for r in recs)
    tot = sum(r.get("trace_seconds", 0) for r in recs)
    if tot > 0:
        easy_ratio = round(below / tot * 100.0, 1)
        easy_ratio_basis = "time"
    else:
        easy_km = sum(v["km"] for v in easy)
        easy_ratio = round(easy_km / total_km * 100.0, 1) if total_km else None
        easy_ratio_basis = "km"

    # WK-09 Edwards TRIMP
    trimp = 0.0
    for r in recs:
        zs = r.get("zone_seconds")
        if zs:
            for i, secs in enumerate(zs):
                trimp += (secs / 60.0) * (i + 1)
    trimp = round(trimp, 1)

    return {
        "week_start": monday.isoformat(),
        "label": f"{monday.day} {MONTHS[monday.month - 1]}",
        "total_km": total_km,                                   # WK-01
        "session_count": len(views),                            # WK-02
        "interval_count": len(interval),
        "easy_count": len(easy),
        "avg_hr": avg_hr,                                        # WK-03
        "weekly_ef": weekly_ef,                                  # WK-04
        "quality_volume_km": qvol,                              # WK-05
        "weekly_sps": weekly_sps,                                # WK-06
        "easy_ratio": easy_ratio,                                # WK-08
        "easy_ratio_basis": easy_ratio_basis,
        "trimp": trimp,                                          # WK-09
        "daily_km": _daily_km(items),
    }


def _daily_km(items):
    days = {}  # iso date -> {interval, easy}
    for v, _ in items:
        d = days.setdefault(v["date_iso"], {"interval": 0.0, "easy": 0.0})
        if v["easy"]:
            d["easy"] += v["km"]
        else:
            d["interval"] += v["km"]
    return days


def _attach_acwr(out_weeks, store):
    """WK-10 ACWR = TRIMP(last 7d) / mean weekly TRIMP(last 28d), at week end."""
    # daily trimp map
    daily = {}
    for r in store.values():
        zs = r.get("zone_seconds")
        if not zs:
            continue
        t = sum((s / 60.0) * (i + 1) for i, s in enumerate(zs))
        daily[r["date_iso"]] = daily.get(r["date_iso"], 0.0) + t

    for week in out_weeks:
        monday = datetime.strptime(week["week_start"], "%Y-%m-%d").date()
        sunday = monday + timedelta(days=6)
        acute = chronic_total = 0.0
        for ds, t in daily.items():
            d = datetime.strptime(ds, "%Y-%m-%d").date()
            if 0 <= (sunday - d).days < 7:
                acute += t
            if 0 <= (sunday - d).days < 28:
                chronic_total += t
        chronic = chronic_total / 4.0
        acwr = round(acute / chronic, 2) if chronic > 0 else None
        week["acwr"] = acwr
        if acwr is None:
            week["acwr_status"] = "na"
        elif acwr > config.THRESHOLDS["acwr_alert"]:
            week["acwr_status"] = "alert"
        elif acwr > config.THRESHOLDS["acwr_high"] or acwr < config.THRESHOLDS["acwr_low"]:
            week["acwr_status"] = "caution"
        else:
            week["acwr_status"] = "good"


# ════════════════════════════════════════════════════════════════════
# Import helpers
# ════════════════════════════════════════════════════════════════════
def import_fit_path(path, store):
    """Parse one FIT file into the store. Returns a status string."""
    stem = os.path.splitext(os.path.basename(path))[0]
    if stem in store:
        return "duplicate"
    record, reason = parse_fit(path)
    if record is None:
        return f"skipped: {reason}"
    store[record["id"]] = record
    return "ok"


# ════════════════════════════════════════════════════════════════════
# Routes
# ════════════════════════════════════════════════════════════════════
@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/api/sessions")
def api_sessions():
    store = load_store()
    views = [build_session_view(r, store) for r in store.values()]
    views.sort(key=lambda v: v["date_iso"], reverse=True)
    return jsonify(views)


@app.route("/api/sessions/<sid>")
def api_session(sid):
    store = load_store()
    if sid not in store:
        return jsonify({"error": "not found"}), 404
    return jsonify(build_session_view(store[sid], store))


@app.route("/api/sessions/<sid>", methods=["PATCH"])
def api_patch_session(sid):
    store = load_store()
    if sid not in store:
        return jsonify({"error": "not found"}), 404
    body = request.get_json(silent=True) or {}
    rec = store[sid]
    if "easy" in body:
        rec["easy"] = bool(body["easy"])
    if "track" in body:
        rec["track"] = bool(body["track"])
    if "theoretical_target" in body:
        rec["theoretical_target"] = body["theoretical_target"] or None
    if "structure" in body:
        rec["structure"] = (body["structure"] or "").strip() or None
    if "lap_types" in body:
        lt = body["lap_types"] or {}
        if isinstance(lt, dict):
            cleaned = {str(int(k)): v for k, v in lt.items()
                       if str(v) in ("wu", "cd", "active", "recovery", "drill")}
            rec["lap_types"] = cleaned
    save_store(store)
    return jsonify(build_session_view(rec, store))


@app.route("/api/upload", methods=["POST"])
def api_upload():
    store = load_store()
    files = request.files.getlist("files[]") or request.files.getlist("files")
    if not files:
        return jsonify({"error": "no files"}), 400
    results = []
    changed = False
    for f in files:
        if not f.filename.lower().endswith(".fit"):
            results.append({"file": f.filename, "status": "skipped: not a .fit file"})
            continue
        dest = os.path.join(FIT_DIR, os.path.basename(f.filename))
        f.save(dest)
        try:
            status = import_fit_path(dest, store)
        except Exception as exc:  # parsing crash on one file must not abort the batch
            status = f"error: {exc}"
        if status == "ok":
            changed = True
        results.append({"file": f.filename, "status": status})
    if changed:
        save_store(store)
    return jsonify({"results": results})


@app.route("/api/scan", methods=["POST"])
def api_scan():
    store = load_store()
    added, skipped, errors = [], [], []
    for name in sorted(os.listdir(FIT_DIR)):
        if not name.lower().endswith(".fit"):
            continue
        path = os.path.join(FIT_DIR, name)
        try:
            status = import_fit_path(path, store)
        except Exception as exc:
            errors.append({"file": name, "reason": str(exc)})
            continue
        if status == "ok":
            added.append(name)
        else:
            skipped.append({"file": name, "reason": status})
    if added:
        save_store(store)
    return jsonify({"added": added, "skipped": skipped, "errors": errors})


@app.route("/api/weekly")
def api_weekly():
    store = load_store()
    return jsonify(compute_weekly(store))


@app.route("/api/config")
def api_config():
    return jsonify(public_config())


@app.route("/api/predictions")
def api_predictions():
    return jsonify(compute_race_predictions(load_store()) or {})


@app.route("/api/sessions/<sid>/export.csv")
def api_export_csv(sid):
    store = load_store()
    if sid not in store:
        return jsonify({"error": "not found"}), 404
    view = build_session_view(store[sid], store)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["type", "rep", "distance_m", "distance_std", "duration_s",
                "pace", "hr_avg", "ea", "cardiac_cost", "hrr60", "rqs",
                "delta_t", "delta_i", "lap_score"])
    for lp in view["laps"]:
        w.writerow([lp.get("type"), lp.get("rep", ""), lp.get("distance_m"),
                    lp.get("distance_std", ""), lp.get("duration_s"),
                    lp.get("pace_fmt"), lp.get("hr_avg", ""), lp.get("ea", ""),
                    lp.get("cardiac_cost", ""), lp.get("hrr60", ""),
                    lp.get("rqs", ""), lp.get("delta_t", ""),
                    lp.get("delta_i", ""), lp.get("lap_score", "")])
    csv_data = buf.getvalue()
    return Response(csv_data, mimetype="text/csv", headers={
        "Content-Disposition": f"attachment; filename={sid}.csv"})


# ── AI chatbot proxy ────────────────────────────────────────────────
@app.route("/api/chat", methods=["POST"])
def chat():
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return jsonify({"error": "AI not configured — add ANTHROPIC_API_KEY to .env"}), 503
    try:
        import anthropic
    except ImportError:
        return jsonify({"error": "AI not configured — anthropic package not installed"}), 503

    body = request.get_json(silent=True) or {}
    store = load_store()
    sid = body.get("session_id")
    if not sid or sid not in store:
        return jsonify({"error": "session not found"}), 404
    view = build_session_view(store[sid], store)
    system = build_context_prompt(view, store)

    try:
        client = anthropic.Anthropic(api_key=key)
        msg = client.messages.create(
            model=CHAT_MODEL,
            max_tokens=500,
            system=system,
            messages=body.get("history", []) + [
                {"role": "user", "content": body.get("message", "")}],
        )
        reply = next((b.text for b in msg.content if b.type == "text"), "")
        return jsonify({"reply": reply})
    except Exception as exc:
        return jsonify({"error": f"AI request failed: {exc}"}), 502


@app.route("/api/chat/stream", methods=["POST"])
def chat_stream():
    """SSE streaming variant of /api/chat (Phase 2)."""
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return jsonify({"error": "AI not configured — add ANTHROPIC_API_KEY to .env"}), 503
    try:
        import anthropic
    except ImportError:
        return jsonify({"error": "AI not configured — anthropic package not installed"}), 503

    body = request.get_json(silent=True) or {}
    store = load_store()
    sid = body.get("session_id")
    if not sid or sid not in store:
        return jsonify({"error": "session not found"}), 404
    view = build_session_view(store[sid], store)
    system = build_context_prompt(view, store)
    messages = body.get("history", []) + [
        {"role": "user", "content": body.get("message", "")}]

    def generate():
        try:
            client = anthropic.Anthropic(api_key=key)
            with client.messages.stream(
                model=CHAT_MODEL, max_tokens=500,
                system=system, messages=messages,
            ) as stream:
                for text in stream.text_stream:
                    yield f"data: {json.dumps({'delta': text})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': f'AI request failed: {exc}'})}\n\n"

    return Response(generate(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",   # disable proxy buffering
    })


@app.route("/api/alerts")
def api_alerts():
    """Complete alert suite (Phase 2) — all thresholds sourced from config."""
    return jsonify({"alerts": compute_alerts(load_store())})


def compute_alerts(store):
    alerts = []
    th = config.THRESHOLDS
    weeks = [w for w in compute_weekly(store)["weeks"] if w["session_count"] > 0]
    if weeks:
        wk = weeks[-1]
        if wk.get("wow_alert"):
            alerts.append({"level": "alert", "kind": "volume",
                "message": f"Weekly volume up {wk['wow_km_pct']:.1f}% vs last week "
                           f"(> {th['weekly_increase_alert']:.0f}%)."})
        if wk.get("acwr_status") == "alert":
            alerts.append({"level": "alert", "kind": "acwr",
                "message": f"ACWR {wk['acwr']:.2f} — injury-risk red zone (> {th['acwr_alert']})."})
        elif wk.get("acwr_status") == "caution":
            alerts.append({"level": "caution", "kind": "acwr",
                "message": f"ACWR {wk['acwr']:.2f} — outside the "
                           f"{th['acwr_low']}–{th['acwr_high']} sweet spot."})
        if wk.get("easy_ratio") is not None and wk["easy_ratio"] < th["easy_ratio_target"]:
            alerts.append({"level": "caution", "kind": "easy_ratio",
                "message": f"Easy ratio {wk['easy_ratio']:.0f}% — below the "
                           f"{th['easy_ratio_target']:.0f}% 80/20 target."})

    views = sorted((build_session_view(r, store) for r in store.values()),
                   key=lambda v: v["date_iso"], reverse=True)
    recent_interval = next((v for v in views if not v["easy"]), None)
    if recent_interval:
        sps = recent_interval["sps_t"] if recent_interval["sps_t"] is not None else recent_interval["sps_i"]
        if sps is not None and sps < th["sps_alert"]:
            alerts.append({"level": "alert", "kind": "sps",
                "message": f"Last interval SPS {sps:.0f} — below {th['sps_alert']} (poor execution)."})
        if recent_interval["pace_fade"] is not None and recent_interval["pace_fade"] > th["pace_fade_alert"]:
            alerts.append({"level": "caution", "kind": "fade",
                "message": f"Last session faded {recent_interval['pace_fade']:.1f}% "
                           f"first→last rep (> {th['pace_fade_alert']:.0f}%)."})
        if recent_interval["hrr60_avg"] is not None and recent_interval["hrr60_avg"] < th["hrr60_alert"]:
            alerts.append({"level": "caution", "kind": "hrr60",
                "message": f"Last session HRR60 {recent_interval['hrr60_avg']:.0f} bpm — "
                           f"below {th['hrr60_alert']} (slow recovery)."})
    recent_easy = next((v for v in views if v["easy"]), None)
    if recent_easy and recent_easy["decoupling"] is not None \
            and recent_easy["decoupling"] > th["decoupling_alert"]:
        alerts.append({"level": "caution", "kind": "decoupling",
            "message": f"Last easy run decoupled {recent_easy['decoupling']:.1f}% "
                       f"(> {th['decoupling_alert']:.0f}%) — possible fatigue or heat."})
    return alerts


# ════════════════════════════════════════════════════════════════════
# Race-time prediction (Riegel)
# ════════════════════════════════════════════════════════════════════
RIEGEL_EXPONENT = 1.06
RACE_DISTANCES = [("5K", 5000.0), ("10K", 10000.0),
                  ("Half", 21097.5), ("Marathon", 42195.0)]


def _riegel(t1, d1, d2):
    """Predict time over d2 from a (t1, d1) effort. Riegel's endurance model."""
    return t1 * (d2 / d1) ** RIEGEL_EXPONENT


def compute_race_predictions(store):
    """Project race times from the best long hard effort in the data.

    Anchors on the longest active rep (≥ 1000 m) across non-easy sessions —
    longer efforts extrapolate to the marathon far more reliably than 400s.
    Returns None when there is no usable anchor.
    """
    candidates = []  # (distance_m, duration_s, date_iso, date, label)
    for r in store.values():
        if r.get("easy"):
            continue
        view = build_session_view(r, store)
        for lp in view["laps"]:
            if lp["type"] != "active":
                continue
            d, t = lp.get("distance_m"), lp.get("duration_s")
            if d and t and d >= 1000 and t > 0:
                candidates.append((d, t, view["date_iso"], view["date"], view["label"]))
    if not candidates:
        return None

    # Anchor = longest effort, tie-broken by faster pace.
    anchor = max(candidates, key=lambda c: (c[0], -c[1] / c[0]))
    a_dist, a_time, _iso, a_date, a_label = anchor
    confidence = ("high" if a_dist >= 5000 else
                  "medium" if a_dist >= 3000 else "low")

    predictions = []
    for name, dist in RACE_DISTANCES:
        t = _riegel(a_time, a_dist, dist)
        predictions.append({
            "name": name,
            "distance_m": dist,
            "time_fmt": fmt_duration(t),
            "pace_fmt": fmt_pace(t / (dist / 1000.0)),
            "pace_sec_km": round(t / (dist / 1000.0), 1),
        })

    goal_pace = config.USER_PROFILE["marathon_target_pace"]
    goal_time = goal_pace * 42.195
    pred_marathon = _riegel(a_time, a_dist, 42195.0)
    return {
        "anchor": {
            "distance_m": round(a_dist),
            "duration_s": round(a_time),
            "pace_fmt": fmt_pace(a_time / (a_dist / 1000.0)),
            "date": a_date,
            "label": a_label,
        },
        "confidence": confidence,
        "predictions": predictions,
        "marathon_goal_fmt": fmt_duration(goal_time),
        "marathon_goal_pace_fmt": fmt_pace(goal_pace),
        "marathon_delta_s": round(pred_marathon - goal_time),
    }


def public_config():
    """Curated config exposed to the frontend so nothing is hard-coded there."""
    p = config.USER_PROFILE
    th = config.THRESHOLDS
    return {
        "weekly_km_target": p["weekly_km_target"],
        "hr_max": p["hr_max"],
        "zone2_ceiling": p["zone2_hr"][1],
        "marathon_target_pace_fmt": fmt_pace(p["marathon_target_pace"]),
        "reference_pace_band": [fmt_pace(config.REFERENCE_PACE_BAND[0]),
                                fmt_pace(config.REFERENCE_PACE_BAND[1])],
        "acwr_low": th["acwr_low"],
        "acwr_high": th["acwr_high"],
        "acwr_alert": th["acwr_alert"],
        "easy_ratio_target": th["easy_ratio_target"],
        "weekly_increase_alert": th["weekly_increase_alert"],
    }


def build_context_prompt(view, store):
    profile = config.USER_PROFILE
    # last 5 same-label sessions for comparison
    same = [v for v in (build_session_view(r, store) for r in store.values())
            if v["label"] == view["label"] and v["id"] != view["id"]]
    same.sort(key=lambda v: v["date_iso"], reverse=True)
    history = [{"date": v["date"], "ef": v["ef"], "decoupling": v["decoupling"],
                "sps_i": v["sps_i"], "pace_fade": v["pace_fade"]} for v in same[:5]]

    compact_laps = [{k: lp.get(k) for k in
                     ("type", "rep", "distance_m", "duration_s", "hr_avg",
                      "pace_fmt", "ea", "hrr60", "rqs", "lap_score")}
                    for lp in view["laps"]]

    return f"""You are a running-analysis assistant for a competitive marathon runner.
Be concise and direct. Ground every statement strictly in the data provided —
if the data is insufficient to answer, say so. Audience is a trained runner:
no beginner platitudes. Use metric units (km, mm:ss/km, bpm). Decline any
medical or injury diagnosis.

RUNNER PROFILE: HR max {profile['hr_max']}, zone-2 ceiling {profile['zone2_hr'][1]} bpm,
threshold HR {profile['threshold_hr']}, marathon target pace {fmt_pace(profile['marathon_target_pace'])}/km.

THIS SESSION ({view['date']}): label={view['label']}, distance={view['km']} km,
duration={view['duration_fmt']}, avg HR={view['avg_hr']}, EF={view['ef']},
decoupling={view['decoupling']}, pace fade={view['pace_fade']}%,
pace CV={view['pace_cv']}%, HRR60 avg={view['hrr60_avg']},
SPS-I={view['sps_i']}, SPS-T={view['sps_t']},
inferred target={view['inferred_target']}, flags: easy={view['easy']} track={view['track']}.

LAPS: {json.dumps(compact_laps, ensure_ascii=False)}

LAST {len(history)} SAME-LABEL SESSIONS: {json.dumps(history, ensure_ascii=False)}
"""


if __name__ == "__main__":
    print("Interval Training Dashboard → http://localhost:5000")
    if not FIT_TOOL_AVAILABLE:
        print("WARNING: fit-tool not installed — FIT parsing disabled. "
              "Run: pip install fit-tool")
    app.run(host="0.0.0.0", port=5000, debug=True)
