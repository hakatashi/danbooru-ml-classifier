#!/usr/bin/env bash
# Install the old-image pruning job as a systemd user timer.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"

mkdir -p "$SYSTEMD_USER_DIR"

for unit in danbooru-prune.service danbooru-prune.timer; do
  sed \
    -e "s|REPO_DIR|$REPO_DIR|g" \
    "$SCRIPT_DIR/$unit" > "$SYSTEMD_USER_DIR/$unit"
  echo "Installed $SYSTEMD_USER_DIR/$unit"
done

systemctl --user daemon-reload
systemctl --user enable --now danbooru-prune.timer
echo "Timer enabled:"
systemctl --user list-timers danbooru-prune.timer --no-pager
