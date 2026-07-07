# Configuration

## Two operating modes

The project offers two modes: a **secured** mode (default) and a **dev** mode.

### Secured mode (recommended)

```bash
make start-secured
```

The MCP server runs inside a Docker container and communicates via SSE (HTTP). It connects to your databases through the shared Docker network `mcp-db-results-anonymizer-network`.

```
AI Agent ──── SSE (port 8080) ──── MCP Container ──── mcp-db-results-anonymizer-network ──── Your databases
(outside)                          (anonymizes)        (shared)          (separate containers)
```

Two MCP registration modes are available:

#### Global (recommended)

```bash
make setup-global
# or separately:
make install-mcp-global
```

Registers the MCP in `~/.claude.json` (scope `user`) via `claude mcp add --scope user`. The MCP will be visible in every Claude Code session, regardless of the working directory.

#### Local

```bash
make setup-local
# or separately:
make install-mcp-local
```

Generates a `.mcp.json` in the project directory. The MCP is only accessible from this directory. The transport mode (SSE or stdio) is automatically detected based on whether the MCP container is running or not.

> **Note**: the `.mcp.json` may contain the API key in plaintext (imposed by the MCP client format). This is why it is listed in `.gitignore` and generated dynamically from the `.env`.

### Dev mode

```bash
make start-dev
```

For project development: the MCP runs locally via `uv run` (stdio), without Docker. Databases must be reachable from localhost (exposed ports or local databases).

```bash
# Stop / remove the MCP container:
make stop           # Stops without removing (quick restart)
make down           # Removes the MCP container
```

After each change, reconnect the MCP in your agent (in Claude Code: `/mcp`).

## Connecting your databases

The MCP is **standalone**: it does not manage database containers. Your databases must be reachable on the Docker network `mcp-db-results-anonymizer-network` (secured mode) or via localhost (dev mode).

```bash
# Create the shared network (once)
docker network create mcp-db-results-anonymizer-network

# Your DB containers must join this network
# Example in your docker-compose.yml:
#   networks:
#     mcp-db-results-anonymizer-network:
#       external: true
```

## config.yaml

The configuration file is externalized at `~/.mcp-db-results-anonymizer/config.yaml` (same location as the `.env`). A template is provided in the repo as `config.example.yaml`.

```bash
cp config.example.yaml ~/.mcp-db-results-anonymizer/config.yaml
```

This file controls:
- Database connections (credentials via `${ENVIRONMENT_VARIABLES}`)
- PII and business detection patterns (extensible)
- Manual sensitivity classification (optional)
- Per-column overrides (force or exclude)
- Security settings (blocked tables, blocked functions, max rows)
- Faker locale for pseudonymization (`fr_FR` by default)

### Connection example

```yaml
databases:
  my_database:
    type: postgresql
    host: mcp_postgres          # container name on mcp-db-results-anonymizer-network
    port: 5432                  # internal Docker port
    user: ${POSTGRES_USER}
    password: ${POSTGRES_PASSWORD}
    database: my_database
```

Hosts use the Docker **container name** (DNS resolution via `mcp-db-results-anonymizer-network`). In dev mode (stdio), use `localhost` and the exposed port.

## Supported databases

| Database | Driver | Read-only | Dependency |
|---|---|---|---|
| PostgreSQL | psycopg2-binary (SQLAlchemy) | `SET default_transaction_read_only = ON` | Included by default |
| MySQL | pymysql (SQLAlchemy) | `SET SESSION TRANSACTION READ ONLY` | `uv sync --extra mysql` |
| SQL Server | pyodbc (SQLAlchemy) | `pyodbc.readonly = True` | `uv sync --extra mssql` |
| MongoDB | pymongo | No write operations in the code | Included by default |

> **Extensibility**: the architecture would allow adding cloud databases (Snowflake, BigQuery, Atlas) but no cloud connector is implemented yet. Cloud credentials would remain isolated inside the MCP container.

## Mapping modes

### `ephemeral` mode (default)

Mappings are **purged after each query**. Pseudonyms are consistent within a single query but not across successive queries. To cross-reference data, use a single query with `JOIN` or `UNION`.

### `session` mode

Mappings are retained for **cross-query consistency**. The same `user_id` will produce the same pseudonym across queries.

- `purgeMappings()` is exposed - call it at the end of your analysis
- Default inactivity TTL of 30 minutes
- Mappings purged on server startup

### Mode configuration

**Via environment variables** (take priority):

```bash
ANONYMIZER_MODE=session           # ephemeral (default) or session
ANONYMIZER_TTL_MINUTES=60         # TTL in minutes (default: 30)
```

**Via `config.yaml`**:

```yaml
storage:
  path: .db-anonymized/mappings.db
  mode: session
  ttl_minutes: 60
```

> **Note**: in secured mode (Docker), this path is resolved inside the container (`/app/.db-anonymized/mappings.db`) and the database is not persisted across restarts. In dev mode, it is created relative to the current working directory.

> **Recommendation**: stay in `ephemeral` mode unless you explicitly need to cross-reference results across multiple queries.

## API key authentication (secured mode)

In secured mode, the server can require a Bearer token for every connection.

> **Production**: without `MCP_API_KEY`, the server accepts **all connections without authentication**.

```bash
# 1. Generate a key and add it to .env (once)
echo "MCP_API_KEY=$(openssl rand -hex 32)" >> ~/.mcp-db-results-anonymizer/.env

# 2. Restart
make stop && make start-secured
```

The key persists across restarts.

### Transport endpoints

In secured mode the server exposes **two transports in parallel** on the same port, so any MCP client works:

| Transport | Endpoint | Used by |
|---|---|---|
| SSE | `http://localhost:8080/sse` | Claude Code, OpenCode, Cursor |
| Streamable HTTP | `http://localhost:8080/mcp` | Codex |

Both are protected by the same Bearer token and rate limiting. `MCP_TRANSPORT=sse` (the default) serves both; only `MCP_TRANSPORT=stdio` disables the network server.

### TLS reverse proxy

For network deployment, see **[REVERSE_PROXY_HELP.md](REVERSE_PROXY_HELP.md)**: nginx + Let's Encrypt configuration.
