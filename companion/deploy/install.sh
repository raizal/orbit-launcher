#!/usr/bin/env bash
# Install / update the Orbit Notes systemd user service.
#   companion/deploy/install.sh          install (or update) and (re)start
# Safe to re-run: it rewrites the unit, syncs deps, and restarts the service.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
companion="$(cd "$here/.." && pwd)"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

dir_of() { command -v "$1" >/dev/null && dirname "$(command -v "$1")" || { echo "missing on PATH: $1" >&2; exit 1; }; }
path="$(dir_of codex):$(dir_of bb):$(dir_of node):$(dir_of ffmpeg):/usr/local/bin:/usr/bin:/bin"

(cd "$companion" && uv sync --quiet)

mkdir -p "$unit_dir"
sed -e "s|@COMPANION_DIR@|$companion|g" -e "s|@PATH@|$path|g" \
  "$here/orbit-notes.service.in" > "$unit_dir/orbit-notes.service"

systemctl --user daemon-reload
systemctl --user enable orbit-notes.service >/dev/null
systemctl --user restart orbit-notes.service
sleep 3
systemctl --user --no-pager --lines=5 status orbit-notes.service | sed -n '1,12p'
