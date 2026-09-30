#!/usr/bin/env python3
"""Claude Code usage indicator for the tmux status bar (tmux-dotbar right side).

Calls the same internal endpoint as Claude Code's /usage screen and renders
the 5-hour and weekly rate-limit windows as "<time until reset> <utilization>",
e.g. "2h 36% · 6d 6%", with tmux style markup in the Twilight palette.

The raw API response is cached (TTL 60s) so the API is hit at most ~once a
minute while tmux redraws every status-interval; countdowns are recomputed on
every invocation so they never go stale. On any failure the last cached
response is rendered instead — never raw error text. The endpoint is
undocumented, so schema drift degrades gracefully to static labels.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CREDS_FILE = Path(os.environ.get("CLAUDE_CREDS_FILE", "~/.claude/.credentials.json")).expanduser()
KEYCHAIN_SERVICE = "Claude Code-credentials"  # where Claude Code stores creds on macOS
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", "~/.cache")).expanduser() / "tmux-claude-usage"
CACHE_FILE = CACHE_DIR / "usage.json"
CACHE_TTL = 60

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"

FG_MUTED = "#8a989a"  # dotbar session grey  (< 70%)
FG_WARN = "#dd7c4c"   # Twilight orange      (>= 70%)
FG_HIGH = "#cf6a4c"   # Twilight red         (>= 90%)


def emit(text):
    print(f"{text} ", end="")
    sys.exit(0)


def emit_muted(text):
    emit(f"#[fg={FG_MUTED}]{text}#[default]")


def countdown(resets_at, hours_only):
    """Single coarse unit until reset: 5h window -> Nh/Nm, weekly -> Nd/Nh."""
    try:
        secs = (datetime.fromisoformat(resets_at) - datetime.now(timezone.utc)).total_seconds()
    except (TypeError, ValueError):
        return None
    secs = max(0, secs)
    if hours_only:
        if secs >= 3600:
            return f"{int(secs / 3600 + 0.5)}h"
        return f"{int(secs / 60 + 0.5)}m"
    if secs >= 86400:
        return f"{int(secs / 86400 + 0.5)}d"
    return f"{int(secs / 3600 + 0.5)}h"


def pct_color(pct):
    if pct >= 90:
        return FG_HIGH
    if pct >= 70:
        return FG_WARN
    return FG_MUTED


def render(usage):
    parts = []
    for key, static_label, hours_only in (("five_hour", "5h", True), ("seven_day", "wk", False)):
        window = usage.get(key) or {}
        pct = window.get("utilization")
        if not isinstance(pct, (int, float)):
            return None
        label = countdown(window.get("resets_at"), hours_only) or static_label
        parts.append(f"#[fg={FG_MUTED}]{label} #[fg={pct_color(pct)}]{round(pct)}%")
    return f"#[fg={FG_MUTED}] · ".join(parts) + "#[default]"


def render_cache_or_warn():
    """Failure path: render from stale cache if possible, else a warning glyph."""
    try:
        out = render(json.loads(CACHE_FILE.read_text()))
        if out:
            emit(out)
    except (OSError, ValueError):
        pass
    emit_muted("claude ⚠")


def read_creds():
    """Credentials file on Linux; macOS keeps them in the login Keychain."""
    try:
        return CREDS_FILE.read_text()
    except OSError:
        if sys.platform != "darwin":
            raise
    return subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
        capture_output=True, text=True, check=True, timeout=3,
    ).stdout


def fetch():
    try:
        creds = json.loads(read_creds())["claudeAiOauth"]
        token = creds["accessToken"]
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        emit_muted("claude ✗")
    # Don't call the API with an expired token (it would 401); running
    # `claude` once refreshes the credentials file.
    expires_ms = creds.get("expiresAt")
    if isinstance(expires_ms, (int, float)) and expires_ms > 0 and time.time() * 1000 >= expires_ms:
        emit_muted("claude ⚠")
    req = urllib.request.Request(USAGE_URL, headers={
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "oauth-2025-04-20",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=3) as resp:
        return resp.read().decode()


def main():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        cache_fresh = time.time() - CACHE_FILE.stat().st_mtime < CACHE_TTL
    except OSError:
        cache_fresh = False

    if not cache_fresh:
        try:
            body = fetch()
            usage = json.loads(body)
        except Exception:
            render_cache_or_warn()
        if render(usage) is None:  # schema drift: don't cache unusable data
            render_cache_or_warn()
        CACHE_FILE.write_text(body)

    try:
        usage = json.loads(CACHE_FILE.read_text())
    except (OSError, ValueError):
        render_cache_or_warn()
    out = render(usage)
    if out is None:
        render_cache_or_warn()
    emit(out)


if __name__ == "__main__":
    main()
