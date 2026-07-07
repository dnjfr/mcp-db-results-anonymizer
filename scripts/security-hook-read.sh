#!/usr/bin/env bash
#
# Security hook - blocks reading files that contain credentials.
# Installed as a PreToolUse hook on the Read tool.
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
  echo "Use MCP tools (query_sql, query_nosql) to access data in an anonymized way." >&2
  exit 2
}

if echo "$FILE_PATH" | grep -qEi '\.mcp-db-results-anonymizer/.env'; then
  block "Reading the .env file containing database credentials is forbidden."
fi

if echo "$FILE_PATH" | grep -qEi '/proc/.*/environ'; then
  block "Reading process environment variables is forbidden."
fi

exit 0
