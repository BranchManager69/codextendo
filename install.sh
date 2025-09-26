#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_DIR="$HOME/.codextendo"
DEST_FILE="$DEST_DIR/codextendo.sh"
SOURCE_SNIPPET='[ -f "$HOME/.codextendo/codextendo.sh" ] && source "$HOME/.codextendo/codextendo.sh"'

mkdir -p "$DEST_DIR"
cp "$REPO_DIR/codextendo.sh" "$DEST_FILE"
chmod +x "$DEST_FILE"

echo "Installed helper script to $DEST_FILE"

append_snippet() {
  local target="$1"
  if [[ -f "$target" ]]; then
    if ! grep -F "$SOURCE_SNIPPET" "$target" >/dev/null 2>&1; then
      {
        echo ""
        echo "# Codextendo helpers"
        echo "$SOURCE_SNIPPET"
      } >> "$target"
      echo "Added sourcing snippet to $target"
    fi
  fi
}

append_snippet "$HOME/.bashrc"
append_snippet "$HOME/.zshrc"

if [[ -f "$HOME/.bash_aliases" ]]; then
  if ! grep -F "$SOURCE_SNIPPET" "$HOME/.bash_aliases" >/dev/null 2>&1; then
    {
      echo ""
      echo "# Codextendo helpers"
      echo "$SOURCE_SNIPPET"
    } >> "$HOME/.bash_aliases"
    echo "Added sourcing snippet to $HOME/.bash_aliases"
  fi
fi

echo "Done. Open a new shell or run 'source ~/.bashrc' (or '~/.zshrc') to enable the helpers."
