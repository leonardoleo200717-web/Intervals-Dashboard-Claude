"""User profile, KPI thresholds and scoring weights.

Every tunable value for the KPI engine and frontend alerts lives here.
Nothing in app.py or the frontend should hard-code a threshold or weight —
read it from this module instead.
"""

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

# ── Distance rounding ranges (distance-based laps only) ───────────────
# (standard_m, track_low, track_high, road_low, road_high)
ROUNDING_TABLE = [
    (400,  360,  440,  350,  449),
    (500,  460,  540,  450,  649),
    (800,  760,  840,  650,  849),
    (1000, 960,  1040, 850,  1149),
    (1200, 1160, 1240, 1150, 1349),
    (1500, 1460, 1540, 1350, 1749),
    (2000, 1960, 2040, 1750, 2499),
]

# ── AI chat providers ─────────────────────────────────────────────────
# The AI coach can talk to any of these. Each provider's API key lives in
# .env (key name below) and is read server-side only — never sent to the
# browser. Only providers whose key is actually set are offered in the UI.
#
#   kind = "anthropic"  → native Anthropic SDK (top-level system prompt)
#   kind = "openai"     → OpenAI-compatible /chat/completions (DeepSeek,
#                         OpenAI, Groq, Together, OpenRouter, Ollama, …);
#                         only base_url + key + model name differ.
#
# `models` is the curated dropdown list; the backend also accepts any other
# model string the client sends, so the list can stay short without locking
# you in. `default_model` is the pre-selected one.
AI_PROVIDERS = {
    "anthropic": {
        "label": "Claude (Anthropic)",
        "kind": "anthropic",
        "env_key": "ANTHROPIC_API_KEY",
        "models": ["claude-sonnet-4-6", "claude-opus-4-8", "claude-haiku-4-5-20251001"],
        "default_model": "claude-sonnet-4-6",
    },
    "deepseek": {
        "label": "DeepSeek",
        "kind": "openai",
        "env_key": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "default_model": "deepseek-chat",
    },
    "openai": {
        "label": "OpenAI",
        "kind": "openai",
        "env_key": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o-mini", "gpt-4o"],
        "default_model": "gpt-4o-mini",
    },
}

# Pre-selected provider when several are configured.
DEFAULT_AI_PROVIDER = "anthropic"

# Cap on the AI reply length (tokens), shared by every provider.
AI_MAX_TOKENS = 500
