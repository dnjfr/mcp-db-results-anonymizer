#!/usr/bin/env bash

# ─────────────────────────────────────────────────────────────
# MCP DB Results Anonymizer - Interactive Installer
# ─────────────────────────────────────────────────────────────

# This installer must be EXECUTED as a file from the cloned repository - never
# sourced and never piped. Sourcing leaks `set -u`/`pipefail` into the caller's
# interactive shell (a prompt that reads $VIRTUAL_ENV then aborts with "unbound
# variable") and makes $0 resolve to /bin/bash, so every SCRIPT_DIR-relative
# path breaks (e.g. //name, /bin/scripts).
if [ -z "${BASH_SOURCE[0]:-}" ] || [ "${BASH_SOURCE[0]}" != "${0}" ]; then
  echo "This installer must be run directly from the cloned repository:"
  echo "    git clone https://github.com/dnjfr/mcp-db-results-anonymizer.git"
  echo "    cd mcp-db-results-anonymizer"
  echo "    bash install.sh"
  echo ""
  echo "(Do not use 'source install.sh' and do not pipe it to bash.)"
  return 1 2>/dev/null || exit 1
fi

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$HOME/.mcp-db-results-anonymizer"
DEFAULT_DIR="db-results-anonymizer-infra"
INSTALL_DIR=""
CHOSEN_AGENT=""

# The installer copies files from the repo (scripts/, infra/Makefile). If they
# are missing we are not in the source tree - fail loudly instead of building
# broken paths.
if [ ! -d "$SCRIPT_DIR/scripts" ] || [ ! -f "$SCRIPT_DIR/infra/Makefile" ]; then
  echo "Error: cannot find the installer's files under: $SCRIPT_DIR"
  echo "Run this script from the cloned repository (bash install.sh)."
  exit 1
fi

# ── Colors ──
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# ── Helpers ──
banner() {
  clear
  echo ""
  echo -e "${CYAN}╔═══════════════════════════════════════════════════════╗${NC}"
  echo -e "${CYAN}║${NC}   ${BOLD}MCP DB Results Anonymizer - Installer${NC}               ${CYAN}║${NC}"
  echo -e "${CYAN}║${NC}   Anonymizing proxy for AI agents + databases         ${CYAN}║${NC}"
  echo -e "${CYAN}╚═══════════════════════════════════════════════════════╝${NC}"
  echo ""
}

step()    { echo ""; echo -e "${GREEN}▶ $1${NC}"; }
info()    { echo -e "  ${CYAN}$1${NC}"; }
warn()    { echo -e "  ${YELLOW}⚠ $1${NC}"; }
err()     { echo -e "  ${RED}✗ $1${NC}"; }
success() { echo -e "  ${GREEN}✓ $1${NC}"; }
ask()     { echo -ne "  $1 "; }
divider() { echo -e "  ${DIM}─────────────────────────────────────────────${NC}"; }

press_enter() {
  echo ""
  ask "Press Enter to continue..."
  read -r
}

# ── JSON / TOML helper functions ──
# Thin bash wrappers around small Python one-liners so every agent recipe
# can manipulate config files without duplicating inline Python blocks.

# Set a key inside a JSON file (creates the file if missing).
# Supports JSONC (strips // comments before parsing) for OpenCode.
# Usage: _json_set_key <file> <dot.path> <json-value> [strip_comments] [defaults_json]
#   _json_set_key mcp.json "mcpServers.my-server" '{"url":"http://..."}'
#   _json_set_key oc.json  "mcp.my-server" '{"type":"remote"}' strip_comments '{"$schema":"..."}'
_json_set_key() {
  local file="$1" dotpath="$2" value="$3" strip="${4:-}" defaults="${5:-"{}"}"
  JSON_DOTPATH="$dotpath" JSON_VALUE="$value" JSON_STRIP="$strip" JSON_DEFAULTS="$defaults" \
  python3 - "$file" <<'PY'
import json, os, re, sys
path = sys.argv[1]
dotpath = os.environ["JSON_DOTPATH"].split(".")
value = json.loads(os.environ["JSON_VALUE"])
strip = os.environ.get("JSON_STRIP", "")
defaults = json.loads(os.environ.get("JSON_DEFAULTS", "{}"))
data = {}
if os.path.exists(path) and os.path.getsize(path) > 0:
    raw = open(path).read()
    if strip:
        raw = re.sub(r'(^|\s)//.*$', '', raw, flags=re.M)
    try:
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        data = {}
for k, v in defaults.items():
    data.setdefault(k, v)
obj = data
for key in dotpath[:-1]:
    obj = obj.setdefault(key, {})
obj[dotpath[-1]] = value
with open(path, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PY
}

# Remove a key from a JSON file. Cleans up empty parent objects.
# Supports JSONC (strips // comments) when strip_comments is set.
# Usage: _json_remove_key <file> <dot.path> [strip_comments]
#   _json_remove_key mcp.json "mcpServers.mcp-db-results-anonymizer"
_json_remove_key() {
  local file="$1" dotpath="$2" strip="${3:-}"
  [ -f "$file" ] || return 0
  JSON_DOTPATH="$dotpath" JSON_STRIP="$strip" \
  python3 - "$file" <<'PY'
import json, os, re, sys
path = sys.argv[1]
dotpath = os.environ["JSON_DOTPATH"].split(".")
strip = os.environ.get("JSON_STRIP", "")
raw = open(path).read()
if strip:
    raw = re.sub(r'(^|\s)//.*$', '', raw, flags=re.M)
try:
    data = json.loads(raw)
except Exception:
    sys.exit(0)
obj = data
parents = []
for key in dotpath[:-1]:
    parents.append((obj, key))
    obj = obj.get(key) or {}
obj.pop(dotpath[-1], None)
for parent, key in reversed(parents):
    if not parent.get(key):
        parent.pop(key, None)
open(path, "w").write(json.dumps(data, indent=2) + "\n")
PY
}

# Check if a dot-path key exists in a JSON file. Returns 0 if found, 1 otherwise.
# Usage: _json_has_key <file> <dot.path>
#   _json_has_key ~/.claude.json "mcpServers.mcp-db-results-anonymizer"
_json_has_key() {
  local file="$1" dotpath="$2"
  [ -f "$file" ] || return 1
  JSON_DOTPATH="$dotpath" \
  python3 - "$file" <<'PY'
import json, os, sys
path = sys.argv[1]
dotpath = os.environ["JSON_DOTPATH"].split(".")
try:
    data = json.load(open(path))
except Exception:
    sys.exit(1)
obj = data
for key in dotpath:
    if not isinstance(obj, dict) or key not in obj:
        sys.exit(1)
    obj = obj[key]
sys.exit(0)
PY
}

# Remove entries from a JSON array where a field contains a pattern, then
# clean up empty parent objects. Used to remove hook entries.
# Usage: _json_remove_from_array <file> <dot.path.to.array> <field> <pattern>
#   _json_remove_from_array hooks.json "hooks.preToolUse" "command" "security-hook.sh"
_json_remove_from_array() {
  local file="$1" dotpath="$2" field="$3" pattern="$4"
  [ -f "$file" ] || return 0
  JSON_DOTPATH="$dotpath" JSON_FIELD="$field" JSON_PATTERN="$pattern" \
  python3 - "$file" <<'PY'
import json, os, sys
path = sys.argv[1]
dotpath = os.environ["JSON_DOTPATH"].split(".")
field = os.environ["JSON_FIELD"]
pattern = os.environ["JSON_PATTERN"]
try:
    data = json.loads(open(path).read())
except Exception:
    sys.exit(0)
obj = data
parents = []
for key in dotpath[:-1]:
    parents.append((obj, key))
    obj = obj.get(key) or {}
arr_key = dotpath[-1]
arr = obj.get(arr_key) or []
arr = [item for item in arr if pattern not in (item.get(field) or "")]
if arr:
    obj[arr_key] = arr
else:
    obj.pop(arr_key, None)
for parent, key in reversed(parents):
    if not parent.get(key):
        parent.pop(key, None)
open(path, "w").write(json.dumps(data, indent=2) + "\n")
PY
}

# Append an entry to a JSON array (creates file + parent objects if missing).
# Usage: _json_append_to_array <file> <dot.path.to.array> <json-value> [defaults_json]
#   _json_append_to_array hooks.json "hooks.preToolUse" '{"command":"..."}' '{"version":1}'
_json_append_to_array() {
  local file="$1" dotpath="$2" value="$3" defaults="${4:-"{}"}"
  JSON_DOTPATH="$dotpath" JSON_VALUE="$value" JSON_DEFAULTS="$defaults" \
  python3 - "$file" <<'PY'
import json, os, sys
path = sys.argv[1]
dotpath = os.environ["JSON_DOTPATH"].split(".")
value = json.loads(os.environ["JSON_VALUE"])
defaults = json.loads(os.environ["JSON_DEFAULTS"])
data = {}
if os.path.exists(path) and os.path.getsize(path) > 0:
    try:
        data = json.loads(open(path).read())
    except Exception:
        data = {}
for k, v in defaults.items():
    data.setdefault(k, v)
obj = data
for key in dotpath[:-1]:
    obj = obj.setdefault(key, {})
obj.setdefault(dotpath[-1], []).append(value)
with open(path, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PY
}

# Remove TOML blocks (starting with a header) that contain a pattern.
# Usage: _toml_remove_block <file> <header_regex> <pattern>
#   _toml_remove_block config.toml '\\[\\[hooks\\.PreToolUse\\]\\]' "security-hook.sh"
_toml_remove_block() {
  local file="$1" header="$2" pattern="$3"
  [ -f "$file" ] || return 0
  TOML_HEADER="$header" TOML_PATTERN="$pattern" \
  python3 - "$file" <<'PY'
import os, re, sys
path = sys.argv[1]
header = os.environ["TOML_HEADER"]
pattern = os.environ["TOML_PATTERN"]
s = open(path).read()
blocks = re.split(r'(?=' + header + ')', s)
open(path, "w").write("".join(b for b in blocks if pattern not in b))
PY
}

# ── Prerequisites ──
check_prerequisites() {
  step "Checking prerequisites..."
  local missing=0
  for cmd in docker curl python3; do
    if ! command -v "$cmd" &>/dev/null; then
      err "$cmd is not installed."
      missing=1
    else
      success "$cmd"
    fi
  done
  if ! docker compose version &>/dev/null; then
    err "docker compose is not available."
    missing=1
  else
    success "docker compose"
  fi
  if [ "$missing" -eq 1 ]; then
    echo ""
    err "Please install the missing prerequisites and re-run this script."
    exit 1
  fi
}

# ── Detect existing installation ──
detect_install_dir() {
  if [ -n "$INSTALL_DIR" ] && [ -d "$INSTALL_DIR" ]; then
    return 0
  fi
  # Try saved path first
  if [ -f "$CONFIG_DIR/infra_path" ]; then
    local saved_path
    saved_path=$(cat "$CONFIG_DIR/infra_path")
    if [ -d "$saved_path" ] && [ -f "$saved_path/docker-compose.yml" ]; then
      INSTALL_DIR="$saved_path"
      return 0
    fi
  fi
  # Fallback: scan sibling directories
  local parent
  parent="$(cd "$SCRIPT_DIR/.." && pwd)"
  for dir in "$parent"/*/; do
    # Must have docker-compose.yml + Makefile with mcp_anonymizer
    # Must NOT have install.sh or src/ (those indicate the source repo)
    if [ -f "${dir}docker-compose.yml" ] && [ -f "${dir}Makefile" ] \
       && [ ! -f "${dir}install.sh" ] && [ ! -d "${dir}src" ]; then
      if grep -q "mcp_anonymizer" "${dir}docker-compose.yml" 2>/dev/null; then
        INSTALL_DIR="${dir%/}"
        echo "$INSTALL_DIR" > "$CONFIG_DIR/infra_path"
        return 0
      fi
    fi
  done
  return 1
}

require_install_dir() {
  if [ -z "$INSTALL_DIR" ] || [ ! -d "$INSTALL_DIR" ]; then
    echo ""
    warn "No installation found. Please run 'Full installation' first (option 1)."
    press_enter
    return 1
  fi
  return 0
}

# ── Status helpers ──
infra_status() {
  if [ -n "$INSTALL_DIR" ] && [ -d "$INSTALL_DIR" ]; then
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "mcp_anonymizer"; then
      echo -e "${GREEN}running${NC}"
    else
      echo -e "${YELLOW}stopped${NC}"
    fi
  else
    echo -e "${DIM}not installed${NC}"
  fi
}

installed_demos() {
  local demos=""
  if docker exec mcp_postgres psql -U "${POSTGRES_USER:-mcp_user}" -lqt 2>/dev/null | grep -q "demo_pagila"; then
    demos="${demos}pagila "
  fi
  if docker exec mcp_postgres psql -U "${POSTGRES_USER:-mcp_user}" -lqt 2>/dev/null | grep -q "demo_chinook"; then
    demos="${demos}chinook "
  fi
  if docker exec mcp_postgres psql -U "${POSTGRES_USER:-mcp_user}" -lqt 2>/dev/null | grep -q "demo_employees"; then
    demos="${demos}employees "
  fi
  if docker exec mcp_postgres psql -U "${POSTGRES_USER:-mcp_user}" -lqt 2>/dev/null | grep -q "demo_adventureworks"; then
    demos="${demos}adventureworks "
  fi
  if docker exec mcp_mongo mongosh --quiet -u "${MONGODB_USER:-mcp_mongo}" -p "${MONGODB_PASSWORD:-}" --authenticationDatabase admin --eval "db.getMongo().getDBNames()" 2>/dev/null | grep -q "demo_ecommerce"; then
    demos="${demos}ecommerce "
  fi
  if [ -z "$demos" ]; then
    echo -e "${DIM}none${NC}"
  else
    echo -e "${CYAN}${demos}${NC}"
  fi
}

# ══════════════════════════════════════════════
# INSTALLATION FUNCTIONS
# ══════════════════════════════════════════════

choose_directory() {
  step "Where should the infrastructure be created?"
  info "It will be created next to this project (../)"
  ask "Directory name [${BOLD}$DEFAULT_DIR${NC}]:"
  read -r dir_name
  dir_name="${dir_name:-$DEFAULT_DIR}"

  INSTALL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)/$dir_name"
  mkdir -p "$CONFIG_DIR"

  if [ -d "$INSTALL_DIR" ]; then
    warn "Directory $INSTALL_DIR already exists."
    ask "Overwrite? (y/N):"
    read -r overwrite
    if [[ ! "$overwrite" =~ ^[yYoO] ]]; then
      info "Keeping existing directory."
      echo "$INSTALL_DIR" > "$CONFIG_DIR/infra_path"
      return
    fi
  fi

  mkdir -p "$INSTALL_DIR"
  echo "$INSTALL_DIR" > "$CONFIG_DIR/infra_path"
  success "→ $INSTALL_DIR"
}

setup_credentials() {
  step "Setting up credentials..."

  if [ -f "$CONFIG_DIR/.env" ]; then
    info "Credentials already exist in $CONFIG_DIR/.env"
    ask "Overwrite? (y/N):"
    read -r overwrite
    if [[ ! "$overwrite" =~ ^[yYoO] ]]; then
      info "Keeping existing credentials."
      return
    fi
  fi

  mkdir -p "$CONFIG_DIR"

  ask "PostgreSQL user [mcp_user]:"
  read -r pg_user
  pg_user="${pg_user:-mcp_user}"

  ask "PostgreSQL password [auto-generated]:"
  read -r pg_pass
  pg_pass="${pg_pass:-$(openssl rand -hex 16)}"

  ask "MongoDB user [mcp_mongo]:"
  read -r mongo_user
  mongo_user="${mongo_user:-mcp_mongo}"

  ask "MongoDB password [auto-generated]:"
  read -r mongo_pass
  mongo_pass="${mongo_pass:-$(openssl rand -hex 16)}"

  local api_key
  api_key="$(openssl rand -hex 32)"

  cat > "$CONFIG_DIR/.env" << EOF
POSTGRES_USER=$pg_user
POSTGRES_PASSWORD=$pg_pass
MONGODB_USER=$mongo_user
MONGODB_PASSWORD=$mongo_pass
MONGO_INITDB_ROOT_USERNAME=$mongo_user
MONGO_INITDB_ROOT_PASSWORD=$mongo_pass
MCP_API_KEY=$api_key
EOF

  chmod 600 "$CONFIG_DIR/.env"
  success "Credentials saved to $CONFIG_DIR/.env"
}

setup_config() {
  if [ -f "$CONFIG_DIR/config.yaml" ]; then
    info "Config already exists at $CONFIG_DIR/config.yaml"
  else
    cp "$SCRIPT_DIR/config.example.yaml" "$CONFIG_DIR/config.yaml"
    success "Config created at $CONFIG_DIR/config.yaml"
  fi
}

generate_compose() {
  step "Generating docker-compose.yml..."

  cat > "$INSTALL_DIR/docker-compose.yml" << 'COMPOSE'
services:

  # --- MCP Server ---

  mcp-server:
    image: dnjfr/mcp-db-results-anonymizer:latest
    pull_policy: always
    container_name: mcp_anonymizer
    ports:
      - "127.0.0.1:8080:8080"
    networks:
      - mcp-db-results-anonymizer-network
    environment:
      # "sse" also serves /mcp (streamable-http) in parallel - see src/server.py:main()
      - MCP_TRANSPORT=sse
      - MCP_PORT=8080
      - MCP_HOST=0.0.0.0
      - MCP_ANON_CONFIG=/app/config.yaml
      - POSTGRES_USER=${POSTGRES_USER}
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - MONGODB_USER=${MONGODB_USER:-}
      - MONGODB_PASSWORD=${MONGODB_PASSWORD:-}
      - MYSQL_USER=${MYSQL_USER:-}
      - MYSQL_PASSWORD=${MYSQL_PASSWORD:-}
      - MSSQL_USER=${MSSQL_USER:-}
      - MSSQL_PASSWORD=${MSSQL_PASSWORD:-}
      - MCP_API_KEY=${MCP_API_KEY:-}
    volumes:
      - ~/.mcp-db-results-anonymizer/config.yaml:/app/config.yaml:ro
    depends_on:
      postgres:
        condition: service_healthy
    restart: unless-stopped

  # --- Databases ---

  postgres:
    image: postgres:16.14-bookworm
    container_name: mcp_postgres
    environment:
      - POSTGRES_USER=${POSTGRES_USER}
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
    networks:
      - mcp-db-results-anonymizer-network
    ports:
      - "5434:5432"
    volumes:
      - ./data/pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready"]
      interval: 5s
      timeout: 5s
      retries: 5

  mongodb:
    image: mongo:7.0
    container_name: mcp_mongo
    environment:
      - MONGO_INITDB_ROOT_USERNAME=${MONGO_INITDB_ROOT_USERNAME}
      - MONGO_INITDB_ROOT_PASSWORD=${MONGO_INITDB_ROOT_PASSWORD}
    networks:
      - mcp-db-results-anonymizer-network
    ports:
      - "27017:27017"
    volumes:
      - ./data/mongodata:/data/db

  mysql:
    image: mysql:8.0
    container_name: mcp_mysql
    profiles: [mysql]
    environment:
      - MYSQL_ROOT_PASSWORD=${MYSQL_PASSWORD:-}
      - MYSQL_USER=${MYSQL_USER:-}
      - MYSQL_PASSWORD=${MYSQL_PASSWORD:-}
    networks:
      - mcp-db-results-anonymizer-network
    ports:
      - "3306:3306"
    volumes:
      - ./data/mysqldata:/var/lib/mysql
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost"]
      interval: 5s
      timeout: 5s
      retries: 5

  mssql:
    image: mcr.microsoft.com/mssql/server:2022-latest
    container_name: mcp_mssql
    profiles: [mssql]
    environment:
      - ACCEPT_EULA=Y
      - MSSQL_SA_PASSWORD=${MSSQL_PASSWORD:-}
    networks:
      - mcp-db-results-anonymizer-network
    ports:
      - "1433:1433"
    volumes:
      - ./data/mssqldata:/var/opt/mssql
    healthcheck:
      test: ["CMD-SHELL", "/opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P \"$${MSSQL_SA_PASSWORD}\" -C -Q 'SELECT 1' || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 5

networks:
  mcp-db-results-anonymizer-network:
    external: true
COMPOSE

  success "docker-compose.yml created"
}

generate_makefile() {
  step "Generating Makefile..."
  if cp "$SCRIPT_DIR/infra/Makefile" "$INSTALL_DIR/Makefile" 2>/dev/null; then
    success "Makefile created"
  else
    warn "Could not find Makefile template. You can copy it manually later."
  fi
}

copy_scripts() {
  step "Copying scripts..."
  mkdir -p "$INSTALL_DIR/scripts"
  cp "$SCRIPT_DIR/scripts/"*.sh "$INSTALL_DIR/scripts/"
  chmod +x "$INSTALL_DIR/scripts/"*.sh

  if [ -f "$SCRIPT_DIR/infra/scripts/update_csvs.py" ]; then
    cp "$SCRIPT_DIR/infra/scripts/update_csvs.py" "$INSTALL_DIR/scripts/"
  fi

  if [ -f "$SCRIPT_DIR/infra/fake_data_mongo.py" ]; then
    cp "$SCRIPT_DIR/infra/fake_data_mongo.py" "$INSTALL_DIR/"
  fi

  success "Security hooks and utility scripts copied"
}

start_infra() {
  step "Starting infrastructure..."
  mkdir -p "$INSTALL_DIR/data"
  # shellcheck disable=SC1091
  set -a
  [ -f "$CONFIG_DIR/.env" ] && source "$CONFIG_DIR/.env" 2>/dev/null || true
  set +a
  docker network create mcp-db-results-anonymizer-network 2>/dev/null || true
  docker compose -f "$INSTALL_DIR/docker-compose.yml" --project-directory "$INSTALL_DIR" up -d

  info "Waiting for PostgreSQL..."
  until docker exec mcp_postgres pg_isready -q 2>/dev/null; do sleep 1; done
  info "Waiting for MongoDB..."
  until docker exec mcp_mongo mongosh --quiet --eval "db.runCommand('ping').ok" 2>/dev/null | grep -q 1; do sleep 1; done

  echo ""
  success "PostgreSQL : localhost:5434"
  success "MongoDB    : localhost:27017"
  success "MCP SSE    : http://localhost:8080/sse"
}

# ══════════════════════════════════════════════
# CODING AGENTS (multi-agent connect / disconnect)
# ══════════════════════════════════════════════
#
# Only Claude Code has a working recipe for now. Codex and OpenCode are shown
# as placeholders and will be wired up in step 2. The MCP server already serves
# both SSE (/sse, used by Claude) and streamable-http (/mcp, used by Codex) in
# parallel, so adding an agent later only means writing its config + hooks here.

agent_label() {
  case "$1" in
    claude)   echo "Claude Code" ;;
    codex)    echo "Codex" ;;
    opencode) echo "OpenCode" ;;
    cursor)   echo "Cursor" ;;
  esac
}

# Returns 0 if the agent has a working recipe, 1 if it is still a placeholder.
agent_supported() {
  case "$1" in
    claude|codex|opencode|cursor) return 0 ;;
    *)                            return 1 ;;
  esac
}

# Returns 0 if the MCP is currently registered for this agent.
agent_is_connected() {
  case "$1" in
    claude)
      if _json_has_key "$HOME/.claude.json" "mcpServers.mcp-db-results-anonymizer"; then
        return 0
      fi
      if [ -n "$INSTALL_DIR" ] && _json_has_key "$INSTALL_DIR/.mcp.json" "mcpServers.mcp-db-results-anonymizer"; then
        return 0
      fi
      if _json_has_key "$SCRIPT_DIR/.mcp.json" "mcpServers.mcp-db-results-anonymizer"; then
        return 0
      fi
      return 1 ;;
    codex)
      if command -v codex >/dev/null 2>&1 && codex mcp get mcp-db-results-anonymizer >/dev/null 2>&1; then
        return 0
      fi
      if [ -f "$HOME/.codex/config.toml" ] \
         && grep -q "mcp-db-results-anonymizer" "$HOME/.codex/config.toml" 2>/dev/null; then
        return 0
      fi
      if [ -n "$INSTALL_DIR" ] && [ -f "$INSTALL_DIR/.codex/config.toml" ] \
         && grep -q "mcp-db-results-anonymizer" "$INSTALL_DIR/.codex/config.toml" 2>/dev/null; then
        return 0
      fi
      return 1 ;;
    opencode)
      local f
      for f in "$HOME/.config/opencode/opencode.json" "$HOME/.config/opencode/opencode.jsonc"; do
        if [ -f "$f" ] && grep -q "mcp-db-results-anonymizer" "$f" 2>/dev/null; then
          return 0
        fi
      done
      if [ -n "$INSTALL_DIR" ]; then
        for f in "$INSTALL_DIR/opencode.json" "$INSTALL_DIR/opencode.jsonc"; do
          if [ -f "$f" ] && grep -q "mcp-db-results-anonymizer" "$f" 2>/dev/null; then
            return 0
          fi
        done
      fi
      return 1 ;;
    cursor)
      if [ -f "$HOME/.cursor/mcp.json" ] \
         && grep -q "mcp-db-results-anonymizer" "$HOME/.cursor/mcp.json" 2>/dev/null; then
        return 0
      fi
      if [ -n "$INSTALL_DIR" ] && [ -f "$INSTALL_DIR/.cursor/mcp.json" ] \
         && grep -q "mcp-db-results-anonymizer" "$INSTALL_DIR/.cursor/mcp.json" 2>/dev/null; then
        return 0
      fi
      return 1 ;;
    *) return 1 ;;
  esac
}

agent_status_label() {
  if ! agent_supported "$1"; then
    echo -e "${DIM}soon${NC}"
    return
  fi
  if agent_is_connected "$1"; then
    echo -e "${GREEN}✓ connected${NC}"
  else
    echo -e "${YELLOW}not connected${NC}"
  fi
}

# --- Claude Code recipe (MCP registration via CLI + security hooks) ---
connect_agent_claude() {
  if ! command -v claude &>/dev/null; then
    warn "Claude Code CLI not found. Install it (or open a new terminal), then retry."
    return 1
  fi

  step "Register MCP in Claude Code"
  ask "Register globally (available in all projects)? (Y/n):"
  read -r global
  global="${global:-y}"

  local api_key
  api_key="$(grep -s '^MCP_API_KEY=' "$CONFIG_DIR/.env" | cut -d= -f2-)"

  if [[ "$global" =~ ^[yYoO] ]]; then
    claude mcp remove mcp-db-results-anonymizer -s user 2>/dev/null || true
    if [ -n "$api_key" ]; then
      claude mcp add mcp-db-results-anonymizer "http://127.0.0.1:8080/sse" \
        -t sse -s user -H "Authorization: Bearer $api_key"
    else
      claude mcp add mcp-db-results-anonymizer "http://127.0.0.1:8080/sse" \
        -t sse -s user
    fi
    success "MCP registered globally (~/.claude.json)."
  else
    local mcp_cfg="$INSTALL_DIR/.mcp.json"
    local entry='{"url":"http://localhost:8080/sse","type":"sse"}'
    if [ -n "$api_key" ]; then
      entry="{\"url\":\"http://localhost:8080/sse\",\"type\":\"sse\",\"headers\":{\"Authorization\":\"Bearer $api_key\"}}"
    fi
    _json_set_key "$mcp_cfg" "mcpServers.mcp-db-results-anonymizer" "$entry"
    success "MCP registered locally ($mcp_cfg)."
  fi

  step "Install Claude Code security hooks"
  bash "$INSTALL_DIR/scripts/install-hooks.sh"
}

disconnect_agent_claude() {
  # Global (user scope)
  if command -v claude &>/dev/null; then
    claude mcp remove mcp-db-results-anonymizer -s user 2>/dev/null || true
    claude mcp remove mcp-db-results-anonymizer -s project 2>/dev/null || true
  fi
  if _json_has_key "$HOME/.claude.json" "mcpServers.mcp-db-results-anonymizer"; then
    _json_remove_key "$HOME/.claude.json" "mcpServers.mcp-db-results-anonymizer"
  fi
  # Local .mcp.json (infra dir)
  if [ -n "$INSTALL_DIR" ] && [ -f "$INSTALL_DIR/.mcp.json" ]; then
    rm -f "$INSTALL_DIR/.mcp.json"
  fi
  # Local .mcp.json (source repo dir)
  if [ -f "$SCRIPT_DIR/.mcp.json" ] && grep -q "mcp-db-results-anonymizer" "$SCRIPT_DIR/.mcp.json" 2>/dev/null; then
    rm -f "$SCRIPT_DIR/.mcp.json"
  fi
  # Hooks
  if [ -f "$INSTALL_DIR/scripts/uninstall-hooks.sh" ]; then
    bash "$INSTALL_DIR/scripts/uninstall-hooks.sh"
  fi
}

# --- Codex recipe (streamable-http /mcp endpoint + TOML PreToolUse hook) ---
connect_agent_codex() {
  if ! command -v codex >/dev/null 2>&1; then
    warn "Codex CLI not found in PATH. Install it (or open a new terminal), then retry."
    return 1
  fi

  step "Register MCP in Codex"
  ask "Register globally (available in all projects)? (Y/n):"
  read -r global
  global="${global:-y}"

  local api_key hook_path toml
  api_key="$(grep -s '^MCP_API_KEY=' "$CONFIG_DIR/.env" | cut -d= -f2-)"
  hook_path="$INSTALL_DIR/scripts/security-hook.sh"

  if [[ "$global" =~ ^[yYoO] ]]; then
    codex mcp remove mcp-db-results-anonymizer >/dev/null 2>&1 || true
    if [ -n "$api_key" ]; then
      codex mcp add mcp-db-results-anonymizer --url http://127.0.0.1:8080/mcp --bearer-token-env-var MCP_API_KEY
    else
      codex mcp add mcp-db-results-anonymizer --url http://127.0.0.1:8080/mcp
    fi
    success "MCP registered globally in ~/.codex/config.toml (streamable-http /mcp)."

    step "Install Codex security hook"
    toml="$HOME/.codex/config.toml"
    if grep -q "security-hook.sh" "$toml" 2>/dev/null; then
      info "Security hook already present in $toml."
    else
      cat >> "$toml" <<EOF

[[hooks.PreToolUse]]
matcher = "^Bash$"

[[hooks.PreToolUse.hooks]]
type = "command"
command = "bash $hook_path"
EOF
      success "PreToolUse hook added to $toml."
    fi
  else
    mkdir -p "$INSTALL_DIR/.codex"
    toml="$INSTALL_DIR/.codex/config.toml"
    echo '[mcp_servers.mcp-db-results-anonymizer]' > "$toml"
    echo 'url = "http://127.0.0.1:8080/mcp"' >> "$toml"
    [ -n "$api_key" ] && echo 'bearer_token_env_var = "MCP_API_KEY"' >> "$toml"
    cat >> "$toml" <<EOF

[[hooks.PreToolUse]]
matcher = "^Bash$"

[[hooks.PreToolUse.hooks]]
type = "command"
command = "bash $hook_path"
EOF
    success "MCP + security hook installed locally in $toml."
  fi

  warn "Codex requires you to trust the hooks on first launch (/hooks)."

  if [ -n "$api_key" ]; then
    echo ""
    warn "Codex reads the token from the MCP_API_KEY env var. Add it to your shell profile:"
    info '  export MCP_API_KEY=$(grep "^MCP_API_KEY=" ~/.mcp-db-results-anonymizer/.env | cut -d= -f2-)'
  fi
}

disconnect_agent_codex() {
  # Global
  command -v codex >/dev/null 2>&1 && codex mcp remove mcp-db-results-anonymizer >/dev/null 2>&1 || true
  local toml="$HOME/.codex/config.toml"
  if [ -f "$toml" ] && grep -q "security-hook.sh" "$toml" 2>/dev/null; then
    _toml_remove_block "$toml" '\[\[hooks\.PreToolUse\]\]' "security-hook.sh"
    info "Codex security hook removed from $toml."
  fi
  # Local
  if [ -n "$INSTALL_DIR" ] && [ -f "$INSTALL_DIR/.codex/config.toml" ]; then
    rm -f "$INSTALL_DIR/.codex/config.toml"
    rmdir "$INSTALL_DIR/.codex" 2>/dev/null || true
    info "Local Codex config removed."
  fi
}

# --- OpenCode recipe (SSE /sse endpoint + JS plugin delegating to the shared hook) ---
opencode_bin() {
  command -v opencode 2>/dev/null || { [ -x "$HOME/.opencode/bin/opencode" ] && echo "$HOME/.opencode/bin/opencode"; }
}

connect_agent_opencode() {
  local oc
  oc="$(opencode_bin)"
  if [ -z "$oc" ]; then
    warn "OpenCode CLI not found. Install it (or open a new terminal), then retry."
    return 1
  fi

  step "Register MCP in OpenCode"
  ask "Register globally (available in all projects)? (Y/n):"
  read -r global
  global="${global:-y}"

  local api_key cfg plugdir scope_label
  api_key="$(grep -s '^MCP_API_KEY=' "$CONFIG_DIR/.env" | cut -d= -f2-)"

  if [[ "$global" =~ ^[yYoO] ]]; then
    local cfgdir="$HOME/.config/opencode"
    cfg="$cfgdir/opencode.json"
    [ -f "$cfgdir/opencode.jsonc" ] && cfg="$cfgdir/opencode.jsonc"
    mkdir -p "$cfgdir"
    plugdir="$cfgdir/plugins"
    scope_label="globally in $cfg"
  else
    cfg="$INSTALL_DIR/opencode.json"
    plugdir="$INSTALL_DIR/.opencode/plugins"
    scope_label="locally in $cfg"
  fi

  local entry='{"type":"remote","url":"http://localhost:8080/sse","enabled":true}'
  if [ -n "$api_key" ]; then
    entry="{\"type\":\"remote\",\"url\":\"http://localhost:8080/sse\",\"enabled\":true,\"headers\":{\"Authorization\":\"Bearer $api_key\"}}"
  fi

  if _json_set_key "$cfg" "mcp.mcp-db-results-anonymizer" "$entry" strip_comments '{"$schema":"https://opencode.ai/config.json"}'; then
    success "MCP registered $scope_label (remote /sse)."
  else
    err "Failed to write the OpenCode config at $cfg."
    return 1
  fi

  step "Install OpenCode security plugin"
  local hook_path="$INSTALL_DIR/scripts/security-hook.sh"
  mkdir -p "$plugdir"
  cat > "$plugdir/mcp-db-anonymizer-guard.js" <<EOF
// Auto-generated by the mcp-db-results-anonymizer installer.
import { spawnSync } from "node:child_process";

const HOOK = "$hook_path";

export const McpDbAnonymizerGuard = async () => ({
  "tool.execute.before": async (input, output) => {
    if (input.tool !== "bash") return;
    const command = output && output.args && output.args.command;
    if (!command) return;
    const res = spawnSync("bash", [HOOK], {
      input: JSON.stringify({ tool_input: { command } }),
      encoding: "utf8",
    });
    if (res.status && res.status !== 0) {
      throw new Error(res.stderr || "Blocked by mcp-db-results-anonymizer security hook");
    }
  },
});
EOF
  success "Security plugin written to $plugdir/mcp-db-anonymizer-guard.js"
}

disconnect_agent_opencode() {
  local cfg
  # Global
  for cfg in "$HOME/.config/opencode/opencode.json" "$HOME/.config/opencode/opencode.jsonc"; do
    _json_remove_key "$cfg" "mcp.mcp-db-results-anonymizer" strip_comments
  done
  rm -f "$HOME/.config/opencode/plugins/mcp-db-anonymizer-guard.js"
  rm -f "$HOME/.config/opencode/plugin/mcp-db-anonymizer-guard.js"
  # Local
  if [ -n "$INSTALL_DIR" ]; then
    for cfg in "$INSTALL_DIR/opencode.json" "$INSTALL_DIR/opencode.jsonc"; do
      _json_remove_key "$cfg" "mcp.mcp-db-results-anonymizer" strip_comments
    done
    rm -f "$INSTALL_DIR/.opencode/plugins/mcp-db-anonymizer-guard.js"
  fi
}

# --- Cursor recipe (SSE /sse endpoint + JSON preToolUse hook) ---
connect_agent_cursor() {
  step "Register MCP in Cursor"
  ask "Register globally (available in all projects)? (Y/n):"
  read -r global
  global="${global:-y}"

  local api_key hook_path cfgdir
  api_key="$(grep -s '^MCP_API_KEY=' "$CONFIG_DIR/.env" | cut -d= -f2-)"
  hook_path="$INSTALL_DIR/scripts/security-hook.sh"

  if [[ "$global" =~ ^[yYoO] ]]; then
    cfgdir="$HOME/.cursor"
  else
    cfgdir="$INSTALL_DIR/.cursor"
  fi
  mkdir -p "$cfgdir"

  local mcp_cfg="$cfgdir/mcp.json"
  local entry='{"url":"http://localhost:8080/sse"}'
  if [ -n "$api_key" ]; then
    entry="{\"url\":\"http://localhost:8080/sse\",\"headers\":{\"Authorization\":\"Bearer $api_key\"}}"
  fi

  if _json_set_key "$mcp_cfg" "mcpServers.mcp-db-results-anonymizer" "$entry"; then
    success "MCP registered in $mcp_cfg (SSE /sse)."
  else
    err "Failed to write MCP config at $mcp_cfg."
    return 1
  fi

  step "Install Cursor security hook"
  local hooks_cfg="$cfgdir/hooks.json"
  _json_remove_from_array "$hooks_cfg" "hooks.preToolUse" "command" "security-hook.sh"
  local hook_entry="{\"command\":\"bash $hook_path\",\"matcher\":\"Shell\",\"failClosed\":true}"
  if _json_append_to_array "$hooks_cfg" "hooks.preToolUse" "$hook_entry" '{"version":1}'; then
    success "preToolUse hook added to $hooks_cfg."
  else
    err "Failed to write hooks config at $hooks_cfg."
    return 1
  fi
}

disconnect_agent_cursor() {
  # Global
  _json_remove_key "$HOME/.cursor/mcp.json" "mcpServers.mcp-db-results-anonymizer"
  _json_remove_from_array "$HOME/.cursor/hooks.json" "hooks.preToolUse" "command" "security-hook.sh"
  # Local
  if [ -n "$INSTALL_DIR" ]; then
    rm -f "$INSTALL_DIR/.cursor/mcp.json" "$INSTALL_DIR/.cursor/hooks.json"
    rmdir "$INSTALL_DIR/.cursor" 2>/dev/null || true
  fi
}

# --- Dispatchers (route to the right recipe per agent) ---
connect_agent() {
  case "$1" in
    claude)   connect_agent_claude ;;
    codex)    connect_agent_codex ;;
    opencode) connect_agent_opencode ;;
    cursor)   connect_agent_cursor ;;
    *) warn "$(agent_label "$1") is not supported." ;;
  esac
}

disconnect_agent() {
  case "$1" in
    claude)   disconnect_agent_claude ;;
    codex)    disconnect_agent_codex ;;
    opencode) disconnect_agent_opencode ;;
    cursor)   disconnect_agent_cursor ;;
    *) : ;;
  esac
}

# Connect if disconnected, offer to disconnect if already connected.
toggle_agent() {
  local agent="$1"
  echo ""
  if ! agent_supported "$agent"; then
    warn "$(agent_label "$agent") support is coming soon."
    info "Claude Code, Codex, OpenCode and Cursor are supported for now."
    press_enter
    return
  fi
  if agent_is_connected "$agent"; then
    step "$(agent_label "$agent") is already connected"
    ask "Disconnect it? (y/N):"
    read -r c
    if [[ "$c" =~ ^[yYoO] ]]; then
      disconnect_agent "$agent"
      success "$(agent_label "$agent") disconnected."
    fi
  else
    connect_agent "$agent"
  fi
  press_enter
}

# Sub-menu shown during a full installation: pick which agent to wire up now.
# Each agent has a different recipe (see connect_agent_*), so we route through
# the shared connect_agent dispatcher and remember the choice for the summary.
choose_agent_for_install() {
  step "Connect your coding agent"
  info "Each agent has its own setup. Pick the one you use:"
  echo ""
  echo "    1) Claude Code"
  echo "    2) Codex"
  echo "    3) OpenCode"
  echo "    4) Cursor"
  echo ""
  echo -e "    ${DIM}0) Skip (connect an agent later from the main menu)${NC}"
  echo ""
  ask "Choose your agent [1]:"
  read -r agent_choice
  agent_choice="${agent_choice:-1}"
  echo ""
  case "$agent_choice" in
    1) CHOSEN_AGENT="claude";   connect_agent claude ;;
    2) CHOSEN_AGENT="codex";    connect_agent codex ;;
    3) CHOSEN_AGENT="opencode"; connect_agent opencode ;;
    4) CHOSEN_AGENT="cursor";   connect_agent cursor ;;
    0) CHOSEN_AGENT=""; info "Skipped. Connect an agent later: main menu → 'Connect a coding agent'." ;;
    *) CHOSEN_AGENT=""; warn "Invalid option - skipping agent setup." ;;
  esac
}

# ══════════════════════════════════════════════
# MENU ACTIONS
# ══════════════════════════════════════════════

do_full_installation() {
  banner

  if detect_install_dir; then
    step "Existing installation detected"
    info "Infrastructure: $INSTALL_DIR"
    echo ""
    echo "    1) Deploy infrastructure"
    echo "    2) Add / manage coding agents"
    echo ""
    echo -e "    ${DIM}0) Back${NC}"
    echo ""
    ask "Choose [0]:"
    read -r sub
    case "$sub" in
      1) ;; # fall through to full installation
      2) agent_menu; return ;;
      0|"") return ;;
      *) return ;;
    esac
  fi

  step "Full Installation"
  echo ""

  choose_directory
  setup_credentials
  setup_config
  generate_compose
  generate_makefile
  copy_scripts
  start_infra

  choose_agent_for_install

  # Load env for status display
  # shellcheck disable=SC1091
  [ -f "$CONFIG_DIR/.env" ] && source "$CONFIG_DIR/.env" 2>/dev/null || true

  echo ""
  echo -e "  ${GREEN}╔═══════════════════════════════════════════════════════╗${NC}"
  echo -e "  ${GREEN}║${NC}   ${BOLD}Installation complete!${NC}                              ${GREEN}║${NC}"
  echo -e "  ${GREEN}╚═══════════════════════════════════════════════════════╝${NC}"
  echo ""
  echo "  Your infrastructure is ready at:"
  echo -e "    ${BOLD}$INSTALL_DIR${NC}"
  echo ""
  echo -e "  ${BOLD}Next steps:${NC}"
  echo -e "    1. Go to your infrastructure directory: ${DIM}cd $INSTALL_DIR${NC}"
  case "$CHOSEN_AGENT" in
    claude)
      echo "    2. Launch Claude Code from there (not from the source repo)"
      echo "    3. Type /mcp → select mcp-db-results-anonymizer → Reconnect"
      ;;
    codex)
      echo "    2. Export the token so Codex can authenticate:"
      echo -e "       ${DIM}export MCP_API_KEY=\$(grep '^MCP_API_KEY=' ~/.mcp-db-results-anonymizer/.env | cut -d= -f2-)${NC}"
      echo "    3. Launch Codex from there, then run /mcp to verify the server."
      ;;
    opencode)
      echo "    2. Launch OpenCode from there (not from the source repo)"
      echo "    3. Run 'opencode mcp list' (or /mcp) to verify the server."
      ;;
    cursor)
      echo "    2. Open the project in Cursor (not the source repo)"
      echo "    3. Check MCP status in Cursor Settings → Tools & MCP"
      ;;
    *)
      echo "    2. Connect an agent later: option 1 → Manage agent connections"
      ;;
  esac
  echo ""
  echo -e "  ${YELLOW}⚠ Do not launch your coding agent from the source repo directory.${NC}"
  echo -e "  ${YELLOW}  It could influence the agent's behavior.${NC}"
  echo ""
  info "You can now install demo databases from the main menu (option 2)."
  press_enter
}

do_install_demos() {
  require_install_dir || return

  banner
  step "Install demo databases"
  echo "  All demos are prefixed with demo_ to avoid conflicts with your data."
  echo ""
  echo "    1) demo_pagila         - Video rental, 600 customers, addresses, payments (PostgreSQL)"
  echo "    2) demo_chinook        - Music store, 60 customers, emails, invoices (PostgreSQL)"
  echo "    3) demo_employees      - HR, 300k employees, salaries, dates of birth (PostgreSQL)"
  echo "    4) demo_adventureworks - E-commerce/HR, 20k people, emails, phones (PostgreSQL)"
  echo "    5) demo_ecommerce      - 10,000 fake customers (MongoDB)"
  echo ""
  echo -e "    ${DIM}0) Back${NC}"
  echo ""
  ask "Which demos to install? (e.g. 1,2,5) [0]:"
  read -r demos
  demos="${demos:-0}"

  if [ "$demos" = "0" ]; then
    return
  fi

  local count=0

  if echo "$demos" | grep -q "1"; then
    echo ""
    info "Installing demo_pagila..."
    if make -C "$INSTALL_DIR" demo-pagila; then
      success "demo_pagila installed."
      count=$((count + 1))
    else
      err "Failed to install demo_pagila."
    fi
  fi

  if echo "$demos" | grep -q "2"; then
    echo ""
    info "Installing demo_chinook..."
    if make -C "$INSTALL_DIR" demo-chinook; then
      success "demo_chinook installed."
      count=$((count + 1))
    else
      err "Failed to install demo_chinook."
    fi
  fi

  if echo "$demos" | grep -q "3"; then
    echo ""
    info "Installing demo_employees (this may take a few minutes)..."
    if make -C "$INSTALL_DIR" demo-employees; then
      success "demo_employees installed."
      count=$((count + 1))
    else
      err "Failed to install demo_employees."
    fi
  fi

  if echo "$demos" | grep -q "4"; then
    echo ""
    info "Installing demo_adventureworks..."
    if make -C "$INSTALL_DIR" demo-adventureworks; then
      success "demo_adventureworks installed."
      count=$((count + 1))
    else
      err "Failed to install demo_adventureworks."
    fi
  fi

  if echo "$demos" | grep -q "5"; then
    echo ""
    info "Installing demo_ecommerce (MongoDB)..."
    if make -C "$INSTALL_DIR" demo-mongo; then
      success "demo_ecommerce installed."
      count=$((count + 1))
    else
      err "Failed to install demo_ecommerce."
    fi
  fi

  if [ "$count" -gt 0 ]; then
    echo ""
    success "$count demo(s) installed successfully."
    info "Reconnect the MCP in Claude Code: /mcp → Reconnect"
  fi

  press_enter
}

do_uninstall_demos() {
  require_install_dir || return

  banner
  step "Uninstall demo databases"
  echo ""
  warn "This will remove ALL demo databases (demo_*)."
  echo "  Your own databases will NOT be affected."
  echo ""
  ask "Are you sure? (y/N):"
  read -r confirm
  if [[ ! "$confirm" =~ ^[yYoO] ]]; then
    info "Cancelled."
    press_enter
    return
  fi

  echo ""
  if make -C "$INSTALL_DIR" uninstall-demos; then
    success "All demo databases have been removed."
  else
    err "An error occurred during uninstallation."
  fi

  press_enter
}

do_import_user_db() {
  require_install_dir || return

  banner
  step "Import your own PostgreSQL database"
  echo ""
  info "Import a .sql or .sql.gz dump into a new database."
  info "You choose the database name - it will NOT be prefixed with demo_."
  echo ""

  ask "Database name (or 'back' to cancel):"
  read -r db_name

  if [ -z "$db_name" ] || [ "$db_name" = "back" ]; then
    return
  fi

  if [[ "$db_name" == demo_* ]]; then
    warn "Names starting with 'demo_' are reserved for demo databases."
    ask "Continue anyway? (y/N):"
    read -r confirm
    if [[ ! "$confirm" =~ ^[yYoO] ]]; then
      return
    fi
  fi

  ask "Path to dump file (.sql or .sql.gz):"
  read -r dump_path

  if [ -z "$dump_path" ]; then
    warn "No path provided."
    press_enter
    return
  fi

  # Expand ~ if present
  dump_path="${dump_path/#\~/$HOME}"

  if [ ! -f "$dump_path" ]; then
    err "File not found: $dump_path"
    press_enter
    return
  fi

  echo ""
  if make -C "$INSTALL_DIR" db-add name="$db_name" dump="$dump_path"; then
    echo ""
    success "Database '$db_name' imported successfully."
    info "Reconnect the MCP in Claude Code: /mcp → Reconnect"
  else
    err "Failed to import database."
  fi

  press_enter
}

do_manage_containers() {
  require_install_dir || return

  while true; do
    banner
    echo -e "  ${BOLD}Stop / Restart Containers${NC}"
    divider
    echo ""
    echo -e "  ${BOLD}Restart${NC}"
    echo "    1) MCP server only"
    echo "    2) Infra only (databases)"
    echo "    3) Everything (MCP server + infra)"
    echo ""
    echo -e "  ${BOLD}Stop${NC}"
    echo "    4) MCP server only"
    echo "    5) Infra only (databases)"
    echo "    6) Everything (MCP server + infra)"
    echo ""
    echo -e "    ${DIM}0) Back to main menu${NC}"
    echo ""
    ask "Choose an option [0]:"
    read -r choice

    case "$choice" in
      1)
        echo ""
        make -C "$INSTALL_DIR" restart
        press_enter
        ;;
      2)
        echo ""
        make -C "$INSTALL_DIR" restart-infra
        press_enter
        ;;
      3)
        echo ""
        make -C "$INSTALL_DIR" restart-all
        press_enter
        ;;
      4)
        echo ""
        make -C "$INSTALL_DIR" stop-mcp
        press_enter
        ;;
      5)
        echo ""
        make -C "$INSTALL_DIR" stop-infra
        press_enter
        ;;
      6)
        echo ""
        make -C "$INSTALL_DIR" stop
        press_enter
        ;;
      0|"") return ;;
      *) warn "Invalid option." ; sleep 1 ;;
    esac
  done
}

# ══════════════════════════════════════════════
# MENUS
# ══════════════════════════════════════════════

demo_menu() {
  while true; do
    banner
    echo -e "  ${BOLD}Demo Databases${NC}"
    divider
    echo ""
    echo "    1) Install demo databases"
    echo "    2) Uninstall all demo databases"
    echo ""
    echo -e "    ${DIM}0) Back to main menu${NC}"
    echo ""
    ask "Choose an option [0]:"
    read -r choice

    case "$choice" in
      1) do_install_demos ;;
      2) do_uninstall_demos ;;
      0|"") return ;;
      *) warn "Invalid option." ; sleep 1 ;;
    esac
  done
}

agent_menu() {
  require_install_dir || return

  while true; do
    banner
    echo -e "  ${BOLD}Connect/Disconnect a coding agent${NC}"
    divider
    echo ""
    echo -e "    1) Claude Code   [$(agent_status_label claude)]"
    echo -e "    2) Codex         [$(agent_status_label codex)]"
    echo -e "    3) OpenCode      [$(agent_status_label opencode)]"
    echo -e "    4) Cursor        [$(agent_status_label cursor)]"
    echo ""
    echo -e "    ${DIM}0) Back to main menu${NC}"
    echo ""
    ask "Choose an agent [0]:"
    read -r choice

    case "$choice" in
      1) toggle_agent claude ;;
      2) toggle_agent codex ;;
      3) toggle_agent opencode ;;
      4) toggle_agent cursor ;;
      0|"") return ;;
      *) warn "Invalid option." ; sleep 1 ;;
    esac
  done
}

main_menu() {
  # Load env for status display
  # shellcheck disable=SC1091
  [ -f "$CONFIG_DIR/.env" ] && source "$CONFIG_DIR/.env" 2>/dev/null || true

  while true; do
    banner

    echo -e "  ${BOLD}Status${NC}"
    divider
    echo -e "  Infrastructure : $(infra_status)"
    if [ -n "$INSTALL_DIR" ] && [ -d "$INSTALL_DIR" ]; then
      echo -e "  Directory      : ${DIM}$INSTALL_DIR${NC}"
    fi
    echo ""

    echo -e "  ${BOLD}Main Menu${NC}"
    divider
    echo ""
    echo "    1) Installation / Agent setup"
    echo "    2) Manage demo databases (install / uninstall)"
    echo "    3) Import your own database"
    echo "    4) Stop / Restart containers"
    echo ""
    echo -e "    ${DIM}0) Quit${NC}"
    echo ""
    ask "Choose an option [0]:"
    read -r choice

    case "$choice" in
      1) do_full_installation ;;
      2) demo_menu ;;
      3) do_import_user_db ;;
      4) do_manage_containers ;;
      0|"") break ;;
      *) warn "Invalid option." ; sleep 1 ;;
    esac
  done
}

quit_message() {
  banner
  if [ -n "$INSTALL_DIR" ] && [ -d "$INSTALL_DIR" ]; then
    echo -e "  ${BOLD}Your infrastructure is at:${NC}"
    echo -e "    ${CYAN}$INSTALL_DIR${NC}"
    echo ""
    echo -e "  ${BOLD}Managing containers:${NC}"
    echo "    To stop or restart containers, re-run this installer"
    echo "    and use option 4 (Stop / Restart)."
    echo ""
    echo -e "  ${BOLD}Other commands:${NC}"
    echo -e "    ${DIM}cd $INSTALL_DIR${NC}"
    echo "    make help             - Show all available commands"
    echo "    make db-add name=X dump=Y - Import your own database"
    echo "    make uninstall-demos  - Remove all demo databases"
    echo ""
    divider
    echo -e "  ${BOLD}Verify your agent connection:${NC}"
    echo "    Claude Code : /mcp → select mcp-db-results-anonymizer → Reconnect"
    echo "    Codex       : /mcp to verify the server"
    echo "    OpenCode    : opencode mcp list (or /mcp)"
    echo "    Cursor      : Settings → Tools & MCP"
    echo ""
  fi
  echo -e "  ${DIM}Goodbye!${NC}"
  echo ""
}

# ══════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════

check_prerequisites
detect_install_dir || true
main_menu
quit_message
