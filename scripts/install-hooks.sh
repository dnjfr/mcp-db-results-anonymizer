#!/usr/bin/env bash
#
# Installs mcp-db-results-anonymizer security hooks
# into Claude Code user settings (~/.claude/settings.json).
#
# Usage: bash scripts/install-hooks.sh

set -euo pipefail

SETTINGS_FILE="$HOME/.claude/settings.json"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -f "$SETTINGS_FILE" ]; then
  echo "File $SETTINGS_FILE not found."
  echo "Run Claude Code once first to create it automatically."
  exit 1
fi

HOOK_BASH="bash $SCRIPT_DIR/security-hook.sh"
HOOK_READ="bash $SCRIPT_DIR/security-hook-read.sh"
HOOK_WRITE="bash $SCRIPT_DIR/security-hook-write.sh"

if grep -q "security-hook.sh" "$SETTINGS_FILE" 2>/dev/null; then
  echo "Security hooks are already installed in $SETTINGS_FILE."
  echo "To reinstall, remove the existing entries first."
  exit 0
fi

python3 << PYEOF
import json, sys

settings_file = "$SETTINGS_FILE"

with open(settings_file) as f:
    settings = json.load(f)

hooks = settings.setdefault("hooks", {})
pre_tool = hooks.setdefault("PreToolUse", [])

new_hooks = [
    {
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": "$HOOK_BASH"}]
    },
    {
        "matcher": "Read",
        "hooks": [{"type": "command", "command": "$HOOK_READ"}]
    },
    {
        "matcher": "Write",
        "hooks": [{"type": "command", "command": "$HOOK_WRITE"}]
    },
    {
        "matcher": "Edit",
        "hooks": [{"type": "command", "command": "$HOOK_WRITE"}]
    },
]

pre_tool.extend(new_hooks)

with open(settings_file, "w") as f:
    json.dump(settings, f, indent=2, ensure_ascii=False)

print(f"4 security hooks added to {settings_file}")
print("Restart Claude Code to activate them.")
PYEOF
