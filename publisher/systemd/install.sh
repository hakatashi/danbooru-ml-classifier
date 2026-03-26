#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
ASDF_SHIMS_DIR="$HOME/.asdf/shims"
ASDF_BIN_DIR="$HOME/.asdf/bin"

mkdir -p "$SYSTEMD_USER_DIR"

for unit in danbooru-fetch.service danbooru-fetch.timer; do
  sed \
    -e "s|REPO_DIR|$REPO_DIR|g" \
    -e "s|ASDF_SHIMS_DIR|$ASDF_SHIMS_DIR|g" \
    -e "s|ASDF_BIN_DIR|$ASDF_BIN_DIR|g" \
    "$SCRIPT_DIR/$unit" > "$SYSTEMD_USER_DIR/$unit"
  echo "Installed $SYSTEMD_USER_DIR/$unit"
done

systemctl --user daemon-reload
systemctl --user enable --now danbooru-fetch.timer
echo "Timer enabled:"
systemctl --user list-timers danbooru-fetch.timer --no-pager
