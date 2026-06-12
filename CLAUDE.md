# Interval Training Dashboard — CLAUDE.md

> **Purpose:** This is a complete build specification for Claude Code. Generate a local-first web dashboard for analysing running interval training from Garmin FIT files. Everything in this document — file structure, parsing logic, KPI formulas, API contracts — is the agreed spec. Build exactly this; where the spec is silent, prefer the simplest working solution.

---

## Quick Start (for Claude Code)

```bash
# Setup
pip install flask fit-tool garminconnect python-dotenv anthropic

# Run
python app.py            # → http://localhost:5000

# Fetch FIT files from Garmin (optional, after configuring .env)
python garmin_sync.py --days 30
```

**Build order:** `config.py` → `app.py` (parsing + API) → `static/index.html` (frontend) → `garmin_sync.py`. Test FIT parsing with a real file before building the frontend on top of it.

**Hard rules:**
- **No fake data, no simulated behaviour.** If a feature can't work (e.g. no API key configured), show a clear error explaining what's missing — never a fake success.
- The Anthropic API key lives **server-side only** (`.env`), never in browser JavaScript.
- All KPI thresholds and SPS weights come from `config.py` — never hard-code them inline.
- All comments and UI text in English. Metric units everywhere (km, mm:ss/km, bpm).

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Files to Generate](#2-files-to-generate)
3. [Tech Stack](#3-tech-stack)
4. [config.py — User Profile & Thresholds](#4-configpy--user-profile--thresholds)
5. [Getting FIT Files from Garmin](#5-getting-fit-files-from-garmin)
6. [FIT File Parsing Logic](#6-fit-file-parsing-logic)
7. [Implementation Pitfalls (read before coding)](#7-implementation-pitfalls)
8. [KPI Definitions](#8-kpi-definitions)
9. [Session Tagging](#9-session-tagging)
10. [Interval Detection Algorithm](#10-interval-detection-algorithm)
11. [Distance Rounding Rules](#11-distance-rounding-rules)
12. [Weekly Report](#12-weekly-report)
13. [AI Chatbot](#13-ai-chatbot)
14. [API Reference](#14-api-reference)
15. [Frontend Views](#15-frontend-views)
16. [Acceptance Criteria](#16-acceptance-criteria)
17. [Development Phases](#17-development-phases)

---

## 1. Project Overview

A **local-first web dashboard** for analysing running interval training sessions. Runs entirely on the user's PC — no cloud hosting, no external database. Parses Garmin FIT files, computes advanced KPIs, and presents an interactive interface for session analysis, historical comparison, and weekly load monitoring.

**The user:** A competitive marathon runner training 5 days/week with 1–2 track interval sessions (400 m – 2000 m repeats, plus time-based intervals of 8–20 min). Goal: marathon at ~3:52/km. The tool exists to answer three questions after every interval session:

1. **How did I execute?** — per-rep pace, HR, and quality score vs target
2. **Am I improving?** — same-session-type comparison over weeks (lower HR at same pace = aerobic progress)
3. **Am I overloading?** — weekly km progression, easy/hard ratio, recovery quality

**Design principles:**
- FIT files are the single source of truth
- Works offline after initial setup (vendor CDN assets locally if practical)
- Honest failure: when something can't be computed (missing HR, no lap data), display "—" with a reason, never invent values

---

## 2. Files to Generate

| File | Description |
|---|---|
| `config.py` | User profile, KPI thresholds, SPS weights (Section 4) |
| `app.py` | Flask backend — FIT parsing, KPI engine, REST API, `/api/chat` proxy |
| `static/index.html` | Complete frontend, single file (HTML + CSS + JS) |
| `garmin_sync.py` | Automated FIT download via unofficial Garmin API |
| `.env.example` | Template: `GARMIN_EMAIL`, `GARMIN_PASSWORD`, `ANTHROPIC_API_KEY` |
| `requirements.txt` | Pinned dependencies |
| `README.md` | Short user-facing setup guide (Italian is fine here — the user is Italian) |

```
dashboard/
├── app.py
├── config.py
├── sessions.json           # auto-created on first import
├── .env                    # user creates from .env.example
├── .env.example
├── garmin_sync.py
├── requirements.txt
├── README.md
├── fit_files/              # FIT drop folder (auto-scan target)
└── static/
    └── index.html
```

---

## 3. Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| Backend | Python 3.11+ · Flask | |
| FIT parsing | `fit-tool` (PyPI) | See pitfalls in Section 7 |
| Session store | `sessions.json` | No database for MVP. Atomic writes (write temp file, rename). |
| Frontend | Vanilla HTML/CSS/JS, single file | No build step |
| Charts | Chart.js 4.x | CDN acceptable; destroy chart instances before re-render |
| AI chatbot | Anthropic API via Flask proxy | `claude-sonnet-4-20250514` or newer |
| Garmin sync | `garminconnect` (PyPI, unofficial) | Same library used by garmin-grafana (3k+ stars) |

---

## 4. config.py — User Profile & Thresholds

All tunable values live here. The KPI engine and frontend alerts read from this file. Generate it with these defaults (calibrated for the actual user):

```python
# ── User training profile ────────────────────────────────────────────
USER_PROFILE = {
    "hr_max": 190,                  # bpm
    "zone2_hr": (125, 143),         # easy run ceiling
    "threshold_hr": 177,            # ~anaerobic threshold HR
    "marathon_target_pace": 232,    # sec/km  (3:52/km — race goal)
    "marathon_fallback_pace": 238,  # sec/km  (3:58/km — acceptable worst case)
    "weekly_km_target": 70,         # progress bar reference on WK-01
}

# ── Aerobic progression tracking ─────────────────────────────────────
REFERENCE_PACE_BAND = (235, 245)   # sec/km — 3:55–4:05/km. HR@RefPace (KPI-07)
                                    # is computed on trace segments inside this band.

# ── HR zones for TRIMP / time-in-zone (fractions of hr_max) ─────────
HR_ZONES = [0.50, 0.60, 0.70, 0.80, 0.90, 1.01]   # z1..z5 boundaries

# ── KPI thresholds (drive alerts and colour coding) ──────────────────
THRESHOLDS = {
    "decoupling_good": 5.0,      # % — below = good (steady sessions only)
    "decoupling_alert": 8.0,     # % — above = orange alert
    "pace_fade_alert": 2.0,      # % — last rep vs first rep, interval sessions
    "pace_cv_alert": 2.0,        # % — rep-pace coefficient of variation
    "hrr60_good": 25,            # bpm drop in 60 s — above = good
    "hrr60_alert": 15,           # bpm — below = flag
    "rqs_good": 75.0,            # % — fallback metric only
    "rqs_alert": 85.0,
    "sps_alert": 50,             # below = red alert
    "weekly_increase_alert": 10.0,  # % week-on-week km
    "easy_ratio_target": 75.0,   # % time below zone-2 ceiling
    "acwr_low": 0.8,             # ACWR sweet spot lower bound
    "acwr_high": 1.3,            # upper bound — above = caution
    "acwr_alert": 1.5,           # red alert
}

# ── SPS / Lap Score weights ──────────────────────────────────────────
# For interval sessions the "decoupling" slot is filled by the Pace Fade score
# (decoupling itself only applies to steady sessions — see KPI-02 note).
SPS_WEIGHTS = {"pace": 0.40, "ef": 0.30, "fade_or_decoupling": 0.20, "recovery": 0.10}
LAP_SCORE_WEIGHTS = {"pace": 0.50, "ef": 0.30, "recovery": 0.20}

# ── Interval detection ───────────────────────────────────────────────
DETECTION = {
    "active_pace_factor": 0.85,   # lap pace < mean*0.85 → candidate active
    "recovery_pace_factor": 1.20, # next lap ≥ 20% slower → recovery
    "rounding_max_m": 2500,       # no rounding above this
    "hr_trace_max_points": 500,   # subsample for chart performance
    "ea_history_window": 8,       # sessions used for EA normalisation
}
```

---

## 5. Getting FIT Files from Garmin

Three approaches, simplest first. Document all three in README.md.

### 5.1 Manual export (no setup, always works)

1. [connect.garmin.com](https://connect.garmin.com) → **Activities** → open an activity
2. Gear icon (⚙) top-right → **Export Original**
3. Extract the downloaded `.zip` → inside is the `.fit` file
4. Drag into the dashboard (**Import FIT**) or copy to `fit_files/` and click **Scan fit_files/**

**Full history bulk export:** Garmin account settings → **Data Management → Export Your Data**. Garmin emails a link to a ZIP with an `Activities/` folder containing every FIT file ever recorded. Extract all into `fit_files/` and scan. This is the recommended path for importing months of history at once.

### 5.2 garmin_sync.py — automated download

Uses the [`garminconnect`](https://github.com/cyberjunky/python-garminconnect) library (reverse-engineered Garmin web session — same approach as [garmin-grafana](https://github.com/arpanghosh8453/garmin-grafana)). Credentials in `.env`, never hardcoded:

```
GARMIN_EMAIL=user@email.com
GARMIN_PASSWORD=secret
```

CLI contract:
```bash
python garmin_sync.py                              # last 30 days
python garmin_sync.py --days 60
python garmin_sync.py --from 2026-04-01 --to 2026-05-31
python garmin_sync.py --limit 10                   # last N activities
python garmin_sync.py --all-types                  # include non-running
python garmin_sync.py --output other_folder
```

Behaviour:
1. Login (or restore cached session from `.garmin_session`, chmod 600)
2. Fetch running activities in range (`get_activities_by_date` / `get_activities`)
3. Download each as ZIP (`ActivityDownloadFormat.ORIGINAL`), extract the `.fit`, delete the ZIP
4. Skip already-downloaded files (filename contains activity ID)
5. Handle `GarminConnectTooManyRequestsError` with a 60 s wait and one retry
6. Print summary: downloaded / skipped / failed

If the account has 2FA, the library prompts for the OTP on first login; the cached session covers subsequent runs. Known limitation: unofficial API, may break if Garmin changes endpoints — fail with a clear message pointing to manual export as fallback.

### 5.3 Future: scheduled sync (garmin-grafana pattern)

Not in scope now, but the architecture should not preclude it: a Dockerized cron container running `garmin_sync.py` every 6 h into the shared `fit_files/` volume, with the Flask app picking new files up via `/api/scan`. Keep `garmin_sync.py` importable (logic in functions, CLI in `main()`) so it can be scheduled later.

---

## 6. FIT File Parsing Logic

Use `fit-tool`. Iterate `FitFile.load(path).records` and dispatch on message type.

**From `SessionMessage`:** `sport`, `sub_sport`, `start_time`, `total_distance` (m), `total_elapsed_time` (s), `avg_heart_rate`.

**From `LapMessage` (one per lap):** `total_distance` (m), `total_elapsed_time` (s), `avg_heart_rate`, `max_heart_rate`, `lap_trigger`, `start_time`.

**From `RecordMessage` (≈1/second):** `timestamp`, `heart_rate` → builds the HR-over-time trace, normalised to seconds from session start and subsampled to ≤ 500 points.

**Running detection:** accept if `sport` or `sub_sport` contains `running` (covers `running`, `track_running`, `treadmill_running`, `trail_running`). Accept `generic`/empty sport if laps have plausible running paces (3:00–8:00/km). Reject others with status `skipped`.

**Pace:** `pace_sec_km = duration_s / (distance_m / 1000)`, only when `distance_m > 50` (avoids divide-by-near-zero on button-mash laps).

**Session ID:** derive from FIT filename stem (which contains the Garmin activity ID) → natural deduplication.

---

## 7. Implementation Pitfalls

Real quirks encountered with these libraries — read before coding:

1. **`lap_trigger` is an enum, not a string.** `str(msg.lap_trigger)` yields e.g. `"LapTrigger.TIME"` or an integer depending on version. Normalise: lowercase, strip everything before the final `.`, compare against `{"time", "distance", "manual", "session_end", "position_start", ...}`. Treat `session_end` as whatever the previous laps were.
2. **Units:** `fit-tool` returns distance in **metres**, time in **seconds**, already scaled. Do not divide by 1000 twice (a classic bug: km values of 0.008).
3. **Timestamps** may be `datetime` objects or epoch numbers depending on field. Guard with `hasattr(x, "isoformat")`.
4. **`avg_heart_rate` can be `None`** (no HR strap / optical failure). Every KPI must tolerate missing HR: render "—", skip from aggregates, never crash.
5. **The final lap is often a stub** — a few seconds triggered by pressing stop. Laps with `duration < 10 s` or `distance < 30 m`: merge into the previous lap or drop.
6. **Treadmill files** have no GPS but valid distance from the accelerometer — parsing must not require GPS records.
7. **Chart.js:** destroy the previous chart instance before creating a new one on the same canvas, or charts stack and flicker on every navigation.
8. **`sessions.json` writes:** write to a temp file then `os.replace()` — a crash mid-write must not corrupt the store.
9. **Flask + large FIT uploads:** set `MAX_CONTENT_LENGTH` to ~50 MB to allow batch uploads.

---

## 8. KPI Definitions

### Session-level

| ID | Name | Formula | Target | Notes |
|---|---|---|---|---|
| KPI-01 | Efficiency Factor (EF) | `speed_m_per_min / hr_avg` | Rising trend | Friel's Efficiency Factor. **Monotonic: higher = better in all cases** (faster at same HR ↑, lower HR at same pace ↑). Replaces the earlier pace/HR "EA" definition, which was ambiguous. |
| KPI-02 | Aerobic Decoupling | `(EF_first_half − EF_second_half) / EF_first_half × 100` | < 5% | **Steady sessions only** (easy runs, continuous tempo). Decoupling is a steady-state metric; on interval sessions HR drift is intentional, so this KPI is suppressed there and replaced by KPI-05/06. |
| KPI-03 | SPS-T | `Σ SPS_WEIGHTS[k] × score_k` with theoretical target | > 75 | Null until user sets a target. |
| KPI-04 | SPS-I | Same with inferred target (median active pace) | > 75 | Always computable. |
| KPI-05 | Pace Fade | `(pace_last_rep − pace_first_rep) / pace_first_rep × 100` | < +2% | Interval sessions only. Positive = slowing down. The primary execution-quality signal for repeats. |
| KPI-06 | Pace Consistency (CV) | `std(rep_paces) / mean(rep_paces) × 100` | < 2% | Interval sessions only. Low CV = even pacing across reps. |
| KPI-07 | HR @ Reference Pace | mean HR of all trace segments where pace ∈ reference band | Decreasing trend | The user's personal aerobic-progress tracker. Reference band from `config.py` (default 3:55–4:05/km). Computed from the second-by-second record trace of **every** session that contains ≥ 3 min in-band; plotted as a long-term trend on Home. Lower HR at the same pace = the core signal of aerobic adaptation. |

> **Which KPIs apply where:** easy/steady sessions → EF + Decoupling + HR@RefPace. Interval sessions → EF (per rep), Pace Fade, Pace CV, HRR60, Cardiac Cost, SPS, HR@RefPace (if warm-up/reps pass through the band). The Home EF trend chart must filter to one session label at a time — never mix labels in one trend line.

### Per-lap (active laps; recovery laps show distance, duration, HR, HRR60/RQS only)

> **HRR60 note:** HRR60 is a validated parasympathetic-reactivation marker and far more robust than averaging recovery-lap HR (which mostly reflects recovery duration, not recovery capacity). Target: > 25 bpm good, 15–25 acceptable, < 15 flag. Thresholds in `config.py`.

| ID | Name | Formula |
|---|---|---|
| LAP-01 | Pace | `duration_s / (distance_m/1000)` — output metric for time-based laps |
| LAP-02 | Avg HR | from FIT |
| LAP-03 | EA per lap | `pace_sec_km / hr_avg` |
| LAP-04 | HRR60 (Heart-Rate Recovery) | `HR_at_rep_end − HR_60s_later`, computed from the second-by-second record trace | 
| LAP-04b | RQS (fallback) | `HR_recovery_lap / HR_preceding_active × 100` — used **only** when the record trace is missing (no per-second HR) |
| LAP-05 | Cardiac Cost | `HR_active − HR_preceding_recovery` |
| LAP-06 | Δ vs theoretical target | `(pace − target)/target × 100`; for time-based laps use duration instead of pace |
| LAP-07 | Δ vs inferred target | same vs session median |
| LAP-08 | Lap Score | `Σ LAP_SCORE_WEIGHTS[k] × score_k` (pace, EA vs session avg, RQS of following recovery) |

### Score functions (piecewise linear, implement in one shared function)

- **Pace score:** Δ 0% → 100 · Δ +3% (slower) → 70 · Δ −3% (faster) → 85 · |Δ| ≥ 10% → 0. Linear between breakpoints. For time-based laps invert sign convention (longer at fixed effort isn't "slower").
- **EA score:** normalised vs the median EA of the last `ea_history_window` sessions **with the same label**. Within ±5% → 75 · +10% → 100 · −10% → 40 · linear between. If < 3 historical sessions exist, EA score = 75 (neutral).
- **Decoupling score:** < 3% → 100 · 3–5% → 80 · 5–8% → 60 · > 8% → 20.
- **Recovery score:** from HRR60 when available: > 30 bpm → 100 · 25–30 → 85 · 15–25 → 60 · < 15 → 30. Fallback to RQS bands when no trace: < 70% → 100 · 70–75% → 80 · 75–85% → 50 · > 85% → 20.
- **Missing components:** renormalise the remaining weights (e.g. no HR → drop EA/RQS terms and scale pace+decoupling weights to sum 1). Show which components were used in a tooltip.

### Weekly (WK-01…WK-08)

| ID | Name | Formula | Target |
|---|---|---|---|
| WK-01 | Total km | sum | progress bar vs `weekly_km_target` |
| WK-02 | Session count | count, split by type | — |
| WK-03 | Avg HR | duration-weighted mean | stable/decreasing |
| WK-04 | Weekly EF (easy runs only) | distance-weighted mean EF of easy sessions | rising over 4–8 wk — mixing easy and interval EF in one number is meaningless, so this is easy-only |
| WK-05 | Interval quality volume | Σ active-lap distance | — |
| WK-06 | Weekly SPS avg | mean SPS-T of interval sessions | > 70 |
| WK-07 | WoW Δ km | `(this−prev)/prev × 100` | < +10% → else alert |
| WK-08 | Easy ratio (time-in-zone) | `time below zone2 ceiling / total time × 100` from HR traces | ≥ 75% — true 80/20 check. Fallback to km-based ratio when traces are missing |
| WK-09 | Weekly TRIMP | Edwards TRIMP: Σ over zones of `minutes_in_zone × zone_index` (zones 1–5 from `hr_max`) | — intensity-weighted load, computed from HR traces |
| WK-10 | ACWR | `TRIMP_last_7d / mean(TRIMP_last_28d)` | 0.8–1.3 sweet spot · > 1.5 → red alert | Acute:chronic workload ratio — the injury-risk monitor. Far more meaningful than raw km% for a runner with shin-splint history. |

Weeks are Monday–Sunday (ISO weeks).

---

## 9. Session Tagging

Two user-editable boolean flags per session, changeable anytime; changes trigger immediate KPI recalculation server-side (PATCH → recompute → return updated session).

| Flags | Interval detection | Per-lap KPIs | Weekly KPIs | Comparison |
|---|---|---|---|---|
| none | on | all | included | enabled |
| **Easy run** | off | none | included | excluded |
| **Track session** | on | all | included | enabled (tight rounding) |
| Easy + Track | off | none | included | excluded |

Badges: Track = blue, Easy = green, Distance-based = purple, Time-based = amber.

---

## 10. Interval Detection Algorithm

Skip entirely when Easy Run flag set.

**Step 1 — warm-up/cool-down:** mean pace of all laps; laps slower than `mean × active_pace_factor` at the start = warm-up, at the end = cool-down; the window between = interval zone. Manual override via UI (PATCH lap type).

**Step 2 — active vs recovery:** within the window, a lap ≥ 20% faster than the next = active; the next = recovery. Garmin lap-button boundaries are authoritative when present.

**Step 3 — time-based vs distance-based:**
- Primary: normalised `lap_trigger` — `time` → time-based (no rounding, raw distance shown); `distance`/`manual` → distance-based.
- Fallback (trigger missing): coefficient of variation. `cv(durations) < 5% and cv(distances) > 10%` → time-based; the inverse → distance-based; ambiguous → distance-based + UI warning "Interval type could not be determined — please verify".
- Manual override per lap or whole session.

**Labels:** `10×400 m` · `6×1 km` · `4×15 min` · mixed → `8 reps (mixed)`.

---

## 11. Distance Rounding Rules

Distance-based laps only; disabled above `rounding_max_m` (2500 m) — long reps like 4.31 km display raw.

| Standard | Track range (tight) | Road range (wide) |
|---|---|---|
| 400 m | 360–440 | 350–449 |
| 500 m | 460–540 | 450–649 |
| 800 m | 760–840 | 650–849 |
| 1000 m | 960–1040 | 850–1149 |
| 1200 m | 1160–1240 | 1150–1349 |
| 1500 m | 1460–1540 | 1350–1749 |
| 2000 m | 1960–2040 | 1750–2499 |

A lap outside every range keeps its raw value in the label (`10×393 m` is wrong; `360–440 → 400` catches it — but a 720 m rep on track stays `720 m`).

---

## 12. Weekly Report

- Header: date range, total km, count badges per type
- WK-01…WK-08 cards with trend arrows vs prior week
- Daily km bar chart (stacked: interval vs easy)
- Rolling 4-week volume chart
- Session list (click → session detail)
- Alert banner when WK-07 > threshold

---

## 13. AI Chatbot

**Architecture (mandatory):** browser → `POST /api/chat` on Flask → Anthropic API. The key is read from `.env` (`ANTHROPIC_API_KEY`) server-side. If the key is missing, `/api/chat` returns 503 with `{"error": "AI not configured — add ANTHROPIC_API_KEY to .env"}` and the UI shows exactly that message. Never call Anthropic from the browser.

**Backend endpoint:**
```python
@app.route("/api/chat", methods=["POST"])
def chat():
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return jsonify({"error": "AI not configured — add ANTHROPIC_API_KEY to .env"}), 503
    body = request.get_json()           # {session_id, message, history: [...]}
    session = find_session(body["session_id"])
    system = build_context_prompt(session)   # see below
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model="claude-sonnet-4-20250514", max_tokens=500,
        system=system,
        messages=body.get("history", []) + [{"role": "user", "content": body["message"]}],
    )
    return jsonify({"reply": msg.content[0].text})
```

**Context prompt** includes: session metadata + flags, all session KPIs, the full per-lap table (compact JSON), the user profile from `config.py` (HR zones, target paces — so answers are calibrated to *this* runner), and the last 5 sessions with the same label (date, EA, decoupling, SPS) for comparison questions.

**System instructions to the model:** concise, direct, grounded strictly in provided data; trained-runner audience (no beginner platitudes); metric units; if data is insufficient, say so; decline medical/injury diagnosis.

**UI:** chat panel in session detail; Enter to send; conversation history kept client-side per session and sent with each request; clear button; visible disclosure: *"Your session data is sent to the AI provider to generate this response."*

---

## 14. API Reference

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | serves `static/index.html` |
| GET | `/api/sessions` | all sessions, newest first |
| GET | `/api/sessions/:id` | one session |
| PATCH | `/api/sessions/:id` | update `easy`, `track`, `theoretical_target` → recompute KPIs → return updated session |
| POST | `/api/upload` | multipart `files[]` of `.fit`; per-file result `ok/duplicate/skipped/error` |
| POST | `/api/scan` | import new files from `fit_files/`; returns added/skipped/errors |
| GET | `/api/weekly` | last 8 ISO weeks aggregated |
| POST | `/api/chat` | AI proxy (Section 13) |
| GET | `/api/sessions/:id/export.csv` | per-lap table as CSV download |

Session JSON shape:
```json
{
  "id": "2026-05-28_Running_12345678901",
  "date": "28 May 2026", "date_iso": "2026-05-28",
  "label": "10×400 m", "km": 8.2, "duration_fmt": "52:14", "avg_hr": 174,
  "easy": false, "track": true, "itype": "distance",
  "ea": 1.31, "decoupling": 4.1, "rqs_avg": 71,
  "sps_t": null, "sps_i": 79,
  "inferred_target": "3:49", "theoretical_target": null,
  "laps": [
    {"type":"wu","distance_m":1200,"duration_s":374,"hr_avg":138,"pace_fmt":"5:12"},
    {"type":"active","rep":1,"distance_m":400,"distance_std":400,"duration_s":91,
     "hr_avg":172,"pace_fmt":"3:48","ea":1.30,"cardiac_cost":32,
     "delta_t":null,"delta_i":"-0.4%","lap_score":81},
    {"type":"recovery","distance_m":200,"duration_s":74,"hr_avg":140,"rqs":69}
  ],
  "hr_trace": [{"t":0,"hr":132}, {"t":5,"hr":135}]
}
```

---

## 15. Frontend Views

Single-page app, sidebar navigation, all in `static/index.html`.

**Home** — this-week KPI cards · **HR @ Reference Pace long-term trend (the headline chart — the user's aerobic-progress tracker)** · EF trend (filtered to one session label, selectable) · SPS trend · ACWR gauge with sweet-spot band · last 3 interval sessions · active alert banners.

**Session detail** — header (label, date, badges, SPS-T/SPS-I) · flag checkboxes + theoretical target input (PATCH on change) · KPI cards · HR-over-time chart with lap boundaries · decoupling panel (first vs second half) · Lap Score sparkline · per-lap table (active rows white, recovery grey, WU/CD italic grey) · CSV export button · AI chat panel.

**History** — filterable list (type, interval type, venue) with badges and SPS pills; click → detail.

**Compare (Phase 2)** — reference + up to 3 sessions, auto-matched by label (time-based by duration ±1 min); overlay charts per rep: pace, HR, EA, Lap Score; delta table with arrows.

**Weekly** — Section 12 layout.

**Import modal** — drag-and-drop + browse, batch, per-file result log; **Scan fit_files/** sidebar action with toast result.

Visual language: warm off-white background (#f8f7f4), teal accent (#1D9E75), DM Sans / DM Mono, minimal borders, status colours green/amber/red as in thresholds. No heavy frameworks.

---

## 16. Acceptance Criteria

The build is done when all of these pass with a **real Garmin FIT file**:

1. `python app.py` starts and prints the URL; `http://localhost:5000` loads with an honest empty state (no placeholder sessions).
2. Uploading a real track-interval FIT yields: correct lap count, correct label (e.g. `10×400 m`), WU/CD excluded, recovery laps grey, plausible paces/HR matching Garmin Connect.
3. Uploading the same file again → `duplicate`, no double entry.
4. Uploading a cycling FIT → `skipped` with reason.
5. A FIT with `lap_trigger=time` (e.g. 4×15 min) shows raw distances (no rounding) and a `N×M min` label.
6. Toggling Easy run hides per-lap KPIs and removes the session from interval views; toggling back restores them. Both survive a server restart (persisted).
7. Setting a theoretical target populates SPS-T and per-lap Δ columns immediately.
8. A FIT without HR data renders with "—" everywhere HR-derived, no errors.
9. `/api/chat` without `ANTHROPIC_API_KEY` → the UI shows the exact "AI not configured" message; with a key, answers reference real numbers from the loaded session.
10. `garmin_sync.py --limit 3` with valid credentials downloads 3 FIT files into `fit_files/`; **Scan fit_files/** imports them.
11. Weekly view shows correct Monday–Sunday totals and the WoW alert fires only above +10%.
12. Kill the server mid-upload, restart: `sessions.json` is intact (atomic writes).
13. An interval session shows Pace Fade and Pace CV; Decoupling appears **only** on easy/steady sessions — verify it is absent from a 10×400 m detail view.
14. HRR60 values appear per rep when the FIT has a second-by-second HR trace; with a trace-less FIT the table falls back to RQS and labels it as such.
15. After importing ≥ 2 weeks of sessions, the Home HR@RefPace chart shows one point per qualifying session and the Weekly view shows TRIMP and ACWR with the 0.8–1.3 band drawn.

---

## 17. Development Phases

**Phase 1 (build now):** everything above except Compare view and streaming chat.

**Phase 2:** Compare view · SSE streaming for chat · alert banner suite complete · CSV export if not done in P1.

**Phase 3:** weekly calendar grid · KPI tooltips · Dockerized scheduled sync (garmin-grafana pattern) · local vendoring of CDN assets for full offline use.

---

*Spec v2.1 — June 2026. Metric revision after coaching review: EA→EF (Friel), decoupling restricted to steady sessions, Pace Fade + Pace CV added for intervals, RQS→HRR60, HR@Reference-Pace tracker added, TRIMP + ACWR load monitoring added. Supersedes SRS v1.2 (docx) as the build reference.*
