#!/usr/bin/env bash
#
# Claude Code security hook - blocks direct database access.
# Installed as a PreToolUse hook on the Bash tool.
#
# Exit codes:
#   0 = allowed
#   2 = blocked (stderr sent to the agent as reason)

set -euo pipefail

INPUT=$(cat)
# Support both stdin schemas: Claude Code and Codex nest the shell command under
# tool_input.command; fall back to a root-level "command" for older payloads.
COMMAND=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); ti=d.get('tool_input') or {}; print(d.get('command') or ti.get('command') or '')" 2>/dev/null)

if [ -z "$COMMAND" ]; then
  exit 0
fi

block() {
  echo "BLOCKED by mcp-db-results-anonymizer security hook: $1" >&2
  echo "Use MCP tools (query_sql, query_nosql) to access data in an anonymized way." >&2
  exit 2
}

# --- Direct database clients ---
if echo "$COMMAND" | grep -qEi '\b(psql|pgcli|pg_dump|pg_restore)\b'; then
  block "Direct PostgreSQL access forbidden. Data must go through the MCP."
fi

if echo "$COMMAND" | grep -qEi '\b(mongosh|mongo|mongodump|mongoexport)\b'; then
  block "Direct MongoDB access forbidden. Data must go through the MCP."
fi

if echo "$COMMAND" | grep -qEi '\b(mysql|mycli|mysqldump)\b'; then
  block "Direct MySQL access forbidden. Data must go through the MCP."
fi

if echo "$COMMAND" | grep -qEi '\b(sqlcmd|bcp)\b'; then
  block "Direct SQL Server access forbidden. Data must go through the MCP."
fi

# --- Docker exec on DB containers ---
if echo "$COMMAND" | grep -qEi 'docker\s+exec.*\b(mcp_postgres|mcp_mongo|mcp_mysql|mcp_mssql|mcp_anonymizer)\b'; then
  block "docker exec on database or MCP containers is forbidden."
fi

# --- Docker inspect / logs / cp (credential or data leakage) ---
if echo "$COMMAND" | grep -qEi 'docker\s+(inspect|logs)\s'; then
  block "docker inspect/logs may expose credentials or sensitive data."
fi

if echo "$COMMAND" | grep -qEi 'docker\s+cp\s'; then
  block "docker cp may exfiltrate data from containers."
fi

if echo "$COMMAND" | grep -qEi 'docker\s+compose\s+config'; then
  block "docker compose config displays resolved credentials."
fi

# --- Reading the .env file containing credentials ---
if echo "$COMMAND" | grep -qEi '(cat|less|more|head|tail|bat|view|nano|vim|vi|code)\s.*\.mcp-db-results-anonymizer/.env'; then
  block "Reading the .env file containing database credentials is forbidden."
fi

if echo "$COMMAND" | grep -qEi '(source|\.)\s.*\.mcp-db-results-anonymizer/.env'; then
  block "Loading the .env file is forbidden."
fi

# --- Reading process environment variables ---
if echo "$COMMAND" | grep -qEi '/proc/.*/environ'; then
  block "Reading process environment variables is forbidden (credential leakage)."
fi

# --- Process tracing (raw data interception) ---
if echo "$COMMAND" | grep -qEi '\b(strace|ltrace|perf\s+trace)\b'; then
  block "Process tracing is forbidden (could intercept raw data)."
fi

# --- Python scripts with DB connection libraries ---
if echo "$COMMAND" | grep -qEi 'python.*-c.*\b(psycopg2|pymongo|pymysql|sqlalchemy)\b'; then
  block "Python script with direct database connection is forbidden."
fi

# --- Potentially exfiltrating Python scripts ---
if echo "$COMMAND" | grep -qEi 'python3?\s+/tmp/'; then
  block "Running Python scripts from /tmp is forbidden (exfiltration risk)."
fi

# --- Intercepting Python scripts that access a DB ---
# Instead of running the script directly (which would expose PII in plain text),
# we block and ask the agent to:
#   1. Read the script to extract the SQL/NoSQL queries
#   2. Run those queries through MCP tools (querySql, queryNosql, etc.)
#   3. Reproduce the business logic with the anonymized results
SCRIPT_PATH=$(echo "$COMMAND" | grep -oEi 'python3?\s+(-[a-zA-Z]\s+)*[^ ]+\.py' | head -1 | sed 's/python3\?\s\+\(-[a-zA-Z]\s\+\)*//' || true)

if [ -n "$SCRIPT_PATH" ] && [ -f "$SCRIPT_PATH" ]; then
  DB_PATTERNS='(psycopg[23]|pymongo|pymysql|pyodbc|pymssql|sqlalchemy|asyncpg|motor)'
  QUERY_PATTERNS='(cursor\.execute|connection\.execute|session\.execute|engine\.execute|\.read_sql|\.read_sql_query|\.read_sql_table|collection\.find|collection\.aggregate|\.fetchall|\.fetchone|\.fetchmany|MongoClient|create_engine|connect\()'

  if grep -qEi "$DB_PATTERNS" "$SCRIPT_PATH" 2>/dev/null || grep -qEi "$QUERY_PATTERNS" "$SCRIPT_PATH" 2>/dev/null; then
    echo "REDIRECT by mcp-db-results-anonymizer hook:" >&2
    echo "Script '$SCRIPT_PATH' contains direct database access." >&2
    echo "Running it directly would return PII in plain text." >&2
    echo "" >&2
    echo "Instead, you must:" >&2
    echo "  1. Read the script with the Read tool to identify the queries" >&2
    echo "  2. Run the queries through MCP tools (querySql, queryNosql...)" >&2
    echo "  3. Reproduce the business logic with the anonymized results" >&2
    exit 2
  fi
fi

exit 0
