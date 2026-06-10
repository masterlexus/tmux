#!/usr/bin/env bash
# Claude Code usage indicator for the tmux status bar (tmux-dotbar right side).
# Calls the same internal endpoint as Claude Code's /usage screen and renders
# 5-hour + weekly utilization with tmux style markup (Twilight palette).
#
# Output example:  5h 42% · wk 13%
# The endpoint is undocumented; on any failure we serve the last cached value
# or a muted warning glyph — never raw error text.

set -u

CREDS_FILE="${CLAUDE_CREDS_FILE:-$HOME/.claude/.credentials.json}"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/tmux-claude-usage"
CACHE_FILE="$CACHE_DIR/status"
CACHE_TTL=60

FG_MUTED="#8a989a"   # dotbar session grey  (< 70%)
FG_WARN="#dd7c4c"    # Twilight orange      (>= 70%)
FG_HIGH="#cf6a4c"    # Twilight red         (>= 90%)

mkdir -p "$CACHE_DIR"

emit() { printf '%s ' "$1"; exit 0; }

muted() { emit "#[fg=$FG_MUTED]$1#[default]"; }

# Serve fresh cache and bail early.
if [[ -f "$CACHE_FILE" ]]; then
    now=$(date +%s)
    mtime=$(stat -c %Y "$CACHE_FILE" 2>/dev/null || echo 0)
    if (( now - mtime < CACHE_TTL )); then
        emit "$(<"$CACHE_FILE")"
    fi
fi

# Stale cache (if any) is the fallback for every failure below.
fallback() {
    if [[ -s "$CACHE_FILE" ]]; then
        emit "$(<"$CACHE_FILE")"
    fi
    muted "claude ⚠"
}

command -v jq >/dev/null && command -v curl >/dev/null || muted "claude ✗"
[[ -r "$CREDS_FILE" ]] || muted "claude ✗"

token=$(jq -r '.claudeAiOauth.accessToken // empty' "$CREDS_FILE" 2>/dev/null)
[[ -n "$token" ]] || muted "claude ✗"

# Don't call the API with an expired token (it would 401); running `claude`
# once refreshes the credentials file.
expires_ms=$(jq -r '.claudeAiOauth.expiresAt // 0' "$CREDS_FILE" 2>/dev/null)
if [[ "$expires_ms" =~ ^[0-9]+$ ]] && (( expires_ms > 0 )); then
    (( $(date +%s) * 1000 < expires_ms )) || muted "claude ⚠"
fi

resp=$(curl -sf --max-time 3 \
    -H "Authorization: Bearer $token" \
    -H "anthropic-beta: oauth-2025-04-20" \
    -H "Content-Type: application/json" \
    "https://api.anthropic.com/api/oauth/usage") || fallback

read -r five seven < <(jq -r \
    '[(.five_hour.utilization // empty), (.seven_day.utilization // empty)]
     | map(round) | @tsv' <<<"$resp" 2>/dev/null) || fallback
[[ -n "${five:-}" && -n "${seven:-}" ]] || fallback

pct_color() {
    if   (( $1 >= 90 )); then printf '%s' "$FG_HIGH"
    elif (( $1 >= 70 )); then printf '%s' "$FG_WARN"
    else                      printf '%s' "$FG_MUTED"
    fi
}

out="#[fg=$FG_MUTED]5h #[fg=$(pct_color "$five")]${five}%#[fg=$FG_MUTED] · wk #[fg=$(pct_color "$seven")]${seven}%#[default]"
printf '%s' "$out" > "$CACHE_FILE"
emit "$out"
