"""Automated FIT download from Garmin Connect.

Uses the unofficial `garminconnect` library (reverse-engineered web session,
the same approach as garmin-grafana). Credentials come from .env, never the
command line. The login session is cached to .garmin_session so 2FA is only
needed once.

Importable by design: all logic lives in functions, the CLI is in main(),
so this module can later be driven by a scheduler (see CLAUDE.md §5.3).

CLI:
    python garmin_sync.py                          # last 30 days
    python garmin_sync.py --days 60
    python garmin_sync.py --from 2026-04-01 --to 2026-05-31
    python garmin_sync.py --limit 10               # last N activities
    python garmin_sync.py --all-types              # include non-running
    python garmin_sync.py --output other_folder
"""

import argparse
import io
import json
import os
import sys
import time
import zipfile
from datetime import date, timedelta

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT = os.path.join(BASE_DIR, "fit_files")
SESSION_DIR = os.path.join(BASE_DIR, ".garmin_session")

RUNNING_TYPES = {"running", "track_running", "treadmill_running", "trail_running"}


def get_client():
    """Login to Garmin, restoring a cached session when possible."""
    try:
        from garminconnect import Garmin
    except ImportError:
        sys.exit("garminconnect not installed — run: pip install garminconnect")

    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")

    # Try restoring a cached token session first (no credentials needed).
    # In garminconnect 0.3.x, login(tokenstore) loads saved tokens from that path.
    if os.path.isdir(SESSION_DIR):
        try:
            client = Garmin()
            client.login(SESSION_DIR)
            return client
        except Exception:
            pass  # tokens missing/expired — fall through to a fresh login

    if not email or not password:
        sys.exit("Set GARMIN_EMAIL and GARMIN_PASSWORD in .env (see .env.example).")

    # Fresh credential login. prompt_mfa is called automatically if the account
    # has 2FA enabled. Passing the tokenstore path to login() makes the library
    # persist the session there itself (there is no client.garth.dump in 0.3.x).
    client = Garmin(email, password, prompt_mfa=lambda: input("Garmin 2FA code: ").strip())
    try:
        client.login(SESSION_DIR)
    except Exception as exc:
        sys.exit(f"Garmin login failed: {exc}\n"
                 "If this persists, use manual export (README §Manual export).")

    # Tighten permissions on the saved token files.
    try:
        os.chmod(SESSION_DIR, 0o700)
        for f in os.listdir(SESSION_DIR):
            try:
                os.chmod(os.path.join(SESSION_DIR, f), 0o600)
            except OSError:
                pass
    except OSError:
        pass
    return client


def fetch_activities(client, days=30, date_from=None, date_to=None, limit=None):
    """Return the list of activity dicts in the requested range."""
    if limit:
        return client.get_activities(0, limit)
    if date_from and date_to:
        return client.get_activities_by_date(date_from, date_to)
    end = date.today()
    start = end - timedelta(days=days)
    return client.get_activities_by_date(start.isoformat(), end.isoformat())


def is_running(activity):
    t = (activity.get("activityType", {}) or {}).get("typeKey", "")
    return any(r in t for r in RUNNING_TYPES) or t == "running"


def download_activity(client, activity, output_dir):
    """Download one activity as ORIGINAL (zip), extract the .fit.

    Returns 'downloaded' | 'skipped' | 'failed'.
    """
    from garminconnect import Garmin

    activity_id = activity["activityId"]
    start = (activity.get("startTimeLocal") or "")[:10] or "activity"
    type_key = (activity.get("activityType", {}) or {}).get("typeKey", "Running")
    stem = f"{start}_{type_key}_{activity_id}"

    # Skip if a FIT with this activity id already exists.
    for name in os.listdir(output_dir):
        if str(activity_id) in name and name.lower().endswith(".fit"):
            return "skipped"

    try:
        data = client.download_activity(
            activity_id, dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL)
    except Exception as exc:
        name = type(exc).__name__
        if "TooManyRequests" in name:
            print("  Rate limited — waiting 60 s then retrying once…")
            time.sleep(60)
            try:
                data = client.download_activity(
                    activity_id, dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL)
            except Exception as exc2:
                print(f"  Failed after retry: {exc2}")
                return "failed"
        else:
            print(f"  Download failed: {exc}")
            return "failed"

    # data is a ZIP byte string; extract the .fit inside.
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            fit_names = [n for n in zf.namelist() if n.lower().endswith(".fit")]
            if not fit_names:
                print("  No .fit inside the downloaded archive.")
                return "failed"
            for n in fit_names:
                target = os.path.join(output_dir, f"{stem}.fit")
                with zf.open(n) as src, open(target, "wb") as dst:
                    dst.write(src.read())
        _write_meta_sidecar(output_dir, stem, activity)
        return "downloaded"
    except zipfile.BadZipFile:
        # Some endpoints return the raw .fit directly.
        target = os.path.join(output_dir, f"{stem}.fit")
        with open(target, "wb") as dst:
            dst.write(data)
        _write_meta_sidecar(output_dir, stem, activity)
        return "downloaded"


def _write_meta_sidecar(output_dir, stem, activity):
    """Save the Garmin activity title next to the FIT.

    The exported FIT does not contain the title the user set in Garmin Connect,
    so the dashboard reads it from "<stem>.meta.json" to let a naming
    convention (e.g. "5x5'", "5x4km p1'") drive interval detection.
    """
    name = activity.get("activityName")
    if not name:
        return
    try:
        with open(os.path.join(output_dir, f"{stem}.meta.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"activity_name": name}, fh, ensure_ascii=False)
    except OSError:
        pass


def sync(days=30, date_from=None, date_to=None, limit=None,
         all_types=False, output_dir=DEFAULT_OUTPUT):
    os.makedirs(output_dir, exist_ok=True)
    client = get_client()
    activities = fetch_activities(client, days, date_from, date_to, limit)

    if not all_types:
        activities = [a for a in activities if is_running(a)]

    downloaded = skipped = failed = 0
    for a in activities:
        name = a.get("activityName", a.get("activityId"))
        print(f"• {name} ({a.get('startTimeLocal', '')[:10]})")
        status = download_activity(client, a, output_dir)
        if status == "downloaded":
            downloaded += 1
        elif status == "skipped":
            skipped += 1
        else:
            failed += 1

    print(f"\nDone: {downloaded} downloaded, {skipped} skipped, {failed} failed.")
    print(f"Files in: {output_dir}")
    print("Now open the dashboard and click 'Scan fit_files/' to import.")
    return {"downloaded": downloaded, "skipped": skipped, "failed": failed}


def main():
    p = argparse.ArgumentParser(description="Download Garmin FIT files.")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--from", dest="date_from")
    p.add_argument("--to", dest="date_to")
    p.add_argument("--limit", type=int)
    p.add_argument("--all-types", action="store_true")
    p.add_argument("--output", default=DEFAULT_OUTPUT)
    args = p.parse_args()
    sync(days=args.days, date_from=args.date_from, date_to=args.date_to,
         limit=args.limit, all_types=args.all_types, output_dir=args.output)


if __name__ == "__main__":
    main()
