#!/usr/bin/env bash
#
# Removes mcp-db-results-anonymizer security hooks
# from Claude Code user settings (~/.claude/settings.json).
#
# Usage: bash scripts/uninstall-hooks.sh

set -euo pipefail

SETTINGS_FILE="$HOME/.claude/settings.json"

if [ ! -f "$SETTINGS_FILE" ]; then
  echo "File $SETTINGS_FILE not found, nothing to uninstall."
  exit 0
fi

if ! grep -q "security-hook" "$SETTINGS_FILE" 2>/dev/null; then
  echo "No mcp-db-results-anonymizer hooks found in $SETTINGS_FILE."
  exit 0
fi

python3 << PYEOF
import json

settings_file = "$SETTINGS_FILE"

with open(settings_file) as f:
    settings = json.load(f)

hooks = settings.get("hooks", {})
pre_tool = hooks.get("PreToolUse", [])

before = len(pre_tool)
pre_tool = [
    h for h in pre_tool
    if not any(
        "security-hook" in hook.get("command", "")
        for hook in h.get("hooks", [])
    )
]
after = len(pre_tool)

hooks["PreToolUse"] = pre_tool
settings["hooks"] = hooks

with open(settings_file, "w") as f:
    json.dump(settings, f, indent=2, ensure_ascii=False)

removed = before - after
if removed:
    print(f"{removed} security hooks removed from {settings_file}")
else:
    print("No hooks to remove.")
PYEOF
