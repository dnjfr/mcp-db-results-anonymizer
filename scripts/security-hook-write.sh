#!/usr/bin/env bash
#
# Security hook - blocks modification of critical files.
# Installed as a PreToolUse hook on the Write and Edit tools.
#
# Exit codes: 0 = allowed, 2 = blocked

set -euo pipefail

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('file_path',''))" 2>/dev/null)

if [ -z "$FILE_PATH" ]; then
  exit 0
fi

block() {
  echo "BLOCKED by mcp-db-results-anonymizer security hook: $1" >&2
  exit 2
}

if echo "$FILE_PATH" | grep -qEi '\.mcp-db-results-anonymizer/.env'; then
  block "Modifying the .env file containing credentials is forbidden."
fi

if echo "$FILE_PATH" | grep -qEi 'security-hook'; then
  block "Modifying security hooks is forbidden."
fi

exit 0
