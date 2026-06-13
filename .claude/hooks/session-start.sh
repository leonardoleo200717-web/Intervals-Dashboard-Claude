#!/bin/bash
# SessionStart hook: install Python dependencies so the dashboard, its
# parsing pipeline and ad-hoc checks work in Claude Code on the web.
set -euo pipefail

# Only run in the remote (web) environment.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"

# Debian ships blinker without a pip RECORD file, so a plain install of Flask
# fails trying to uninstall it. --ignore-installed blinker sidesteps that.
python3 -m pip install --quiet --ignore-installed blinker -r requirements.txt

echo "Dependencies installed."
