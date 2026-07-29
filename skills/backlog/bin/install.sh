#!/usr/bin/env bash
# Optionally install the containing skill directly for Codex and Claude Code.
# Plugin/marketplace installation does not run this script.
set -euo pipefail

SKILL="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

chmod +x "$SKILL/bin/backlog" "$SKILL/bin/backlog-py"

link() {
  local dest="$1"
  mkdir -p "$(dirname "$dest")"
  if [ -L "$dest" ]; then
    rm "$dest"
  elif [ -e "$dest" ]; then
    echo "install: $dest exists and is not a symlink; move it aside first." >&2
    return 1
  fi
  ln -s "$SKILL" "$dest"
  echo "  $dest -> $SKILL"
}

echo "linking the skill:"
link "$HOME/.codex/skills/backlog"
link "$HOME/.claude/skills/backlog"

echo "provisioning the environment:"
"$SKILL/bin/backlog" --version
