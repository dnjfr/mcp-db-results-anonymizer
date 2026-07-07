# Security

This document describes the threat model, implemented protections,
identified attack vectors, and known limitations of the project.

## Threat model

The MCP server is a proxy between an AI agent and databases.
The agent is considered **untrusted**: if a technical path exists
to access raw data, it will eventually take it - even without malicious intent.

> **Founding observation** (06/29/2026): during a test on a production database,
> the AI agent bypassed the MCP by executing a direct `docker exec` on the
> PostgreSQL container. Real personal data transited through the AI provider's
> servers. The agent did this on its own initiative.
> Conclusion: security must be **architectural**, not behavioral.

## Security architecture: the trust boundary

The MCP container is the **trust boundary**. Everything sensitive
(credentials, raw data) stays inside. The AI agent is outside
and communicates only via SSE - it never sees the real data.

```
┌──────────────────────────────────────────────────────────────────┐
│                        OUTSIDE (untrusted)                       │
│                                                                  │
│   AI Agent (Claude, GPT, Cursor...)                              │
│   ├── Does NOT have credentials                                  │
│   ├── Does NOT have access to DB ports (not exposed in secured mode)│
│   ├── Does NOT have access to the Docker socket (not mounted)    │
│   ├── Does NOT have access to data volumes (managed separately)  │
│   └── Communicates ONLY via SSE (port 8080, Bearer auth)         │
│                                │                                 │
│                           SSE + API key                          │
│                                │                                 │
├────────────────────────────────┼─────────────────────────────────┤
│                         INSIDE (trusted)                         │
│                                │                                 │
│                                ▼                                 │
│   MCP Server Container                                           │
│   ├── Credentials injected via env_file (not exposed on host)    │
│   ├── Connects to DBs via shared Docker network (mcp-db-results-anonymizer-network)│
│   ├── Receives RAW data                                          │
│   ├── Anonymizes via the pipeline (5 detection layers)           │
│   └── Returns ONLY pseudonymized data                            │
│                  │                         │                     │
│             ┌────┴──────┐           ┌──────┴──────┐              │
│             │ Local DB  │           │ Cloud DB    │              │
│             │ (Docker   │           │ (Snowflake, │              │
│             │  shared   │           │  BigQuery,  │              │
│             │  network) │           │  Atlas)     │              │
│             └───────────┘           └─────────────┘              │
└──────────────────────────────────────────────────────────────────┘
```

### What containerization solves

| Vector category | Solved? | How |
|---|---|---|
| Direct DB access (`docker exec`, `psql`, `mongosh`) | Yes | No access to Docker socket + ports not exposed (if configured accordingly) |
| Credential exfiltration (`.env`, `docker inspect`, `/proc/*/environ`) | Yes | Credentials only inside the MCP container |
| Exfiltration scripts (`python -c "import psycopg2..."`) | Yes | No credentials + no network route to DBs (isolated Docker network) |
| Reading data volumes (`pgdata`, `mongodata`) | Yes | Volumes managed by the DB docker-compose (separate from MCP) |
| Modifying source code or config | Yes | Code baked into the image, `config.yaml` mounted as `:ro` |
| Cloud credentials (service accounts, tokens) | Yes | Files and env vars only inside the container |

### What containerization does NOT solve

- **MCP tool abuse**: SQL aliases, encoding, concatenation are vectors
  that bypass PII detection, not the architecture. Solved by
  code (sqlglot lineage tracing, blocking `encode`/`convert_from`), not infra.
- **PII in the query itself**: if the agent writes
  `WHERE email = 'jean@example.com'`, the email transits. Out of scope.

### Why hooks are not enough

Coding-agent hooks intercept shell commands before execution.
They block `docker exec`, `psql`, `cat .env`, etc. But:

- The agent can **rephrase**: `bash -c "ps\ql"`, variables, heredocs, base64...
- The agent can **write a script** in Python that does the same thing
- Hooks only protect the agents where they are installed (Claude Code,
  Codex, OpenCode, Cursor) - another agent (Copilot) using the same MCP
  would not have these hooks

Hooks are a **safety net** (they block bypass attempts by reflex),
but they do not constitute a security solution. Network isolation is the real wall.

## Defense in depth strategy

```
Layer 1 - Architecture (the wall)
  └── Containerized MCP, isolated credentials, unexposed ports,
      API key authentication, localhost binding

Layer 2 - Hooks (the safety net)
  └── Block docker exec, psql, mongosh, .env reading,
      config.yaml modification, etc.

Layer 3 - Detection pipeline (the scan)
  └── Pattern matching on column names + SQL lineage tracing
      (sqlglot) + fallback value scan on results

Layer 4 - Query validation (the filter)
  └── Read-only, multi-statements blocked, dangerous functions
      blocked per dialect, system tables forbidden
```

Each layer compensates for the others' weaknesses:
- If hooks are bypassed → the container blocks
- If a SQL alias evades name-based detection → the value scan catches it
- If the value scan misses a pattern → hooks block direct access

---

## Implemented protections

### Transport and network

| Protection | Detail |
|---|---|
| **Localhost binding** | The server listens on `127.0.0.1` by default (not `0.0.0.0`). In Docker, exposure is limited to `127.0.0.1:8080` |
| **API key authentication** | Bearer token middleware on `/sse` and `/messages/` endpoints. Key configured via `MCP_API_KEY` in `.env`. Timing-safe comparison (`hmac.compare_digest`) |
| **Shared Docker network** | The MCP joins the `mcp-db-results-anonymizer-network` network to reach databases. Databases are not in the same docker-compose - they are managed separately |
| **DB ports not exposed** | In secured mode, databases should not expose ports on the host (admin's responsibility) |
| **TLS reverse proxy** | Documentation provided for nginx + Let's Encrypt (`REVERSE_PROXY_HELP.md`) |

### API key authentication

In SSE mode (Docker), the server can require a Bearer token:

```bash
# Generate a key (pure ASCII, required - accents are not supported in HTTP headers)
echo "MCP_API_KEY=$(openssl rand -hex 32)" >> ~/.mcp-db-results-anonymizer/.env
```

Behavior:
- `MCP_API_KEY` set → any request without `Authorization: Bearer <key>` is rejected (401/403)
- `MCP_API_KEY` not set → warning in logs, connections accepted (backward compatibility)
- In stdio mode (local) → no verification (no network)

The `make install-mcp-local` command automatically injects the header into `.mcp.json`.

### Logging and audit

Every MCP tool call is traced in the logs:

```
2026-06-30 09:15:23 | mcp.server | INFO | querySql | database=pagila | query=SELECT * FROM customer...
2026-06-30 09:15:23 | mcp.server | INFO | querySql | database=pagila | 42 rows | 85ms
```

Rejected authentication attempts are also logged with the source IP address.

### Query validation

| Protection | Dialects | Detail |
|---|---|---|
| **Read-only enforced by DB engine** | PG, MySQL, MSSQL | Read-only transaction at driver level |
| **Write keywords blocked** | All | INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, COPY, CREATE, GRANT, REVOKE |
| **Multi-statements blocked** | All | `;` forbidden except at end of query |
| **Dangerous functions** | PG | `pg_read_file`, `dblink`, `lo_import`, `encode`, `convert_from` |
| | MySQL | `LOAD_FILE`, `INTO OUTFILE`, `BENCHMARK`, `SLEEP` |
| | MSSQL | `xp_cmdshell`, `OPENROWSET`, `sp_configure` |
| **System tables** | PG | `pg_shadow`, `pg_authid`, `pg_roles`, `information_schema` |
| | MySQL | `mysql.user`, `mysql.db`, `performance_schema` |
| | MSSQL | `sys.sql_logins`, `sys.server_principals`, `sys.credentials` |
| **Validated identifiers** | All | `validate_identifier()` - alphanumeric + underscore only |
| **Dangerous NoSQL operators** | MongoDB | `$where`, `$function`, `$accumulator`, `$expr` blocked |
| **NoSQL write stages** | MongoDB | `$out`, `$merge` blocked in aggregation pipelines |

### Anonymization pipeline

5 detection layers (manual classification, pattern matching, value scan, SQL lineage, fallback value scan). Fail-closed: if detection fails, the query is blocked.

Full details in [DETECTION.md](DETECTION.md).

### Other protections

- **Isolated credentials**: `.env` in `~/.mcp-db-results-anonymizer/`, outside the project
- **Hashed mapping**: real values hashed with SHA-256 in SQLite (irreversible)
- **Auto cleanup**: session mappings cleared on server shutdown
- **Sanitized error messages**: emails, phones, IPs, `Key=(value)` patterns masked
- **Read-only config.yaml**: mounted as `:ro` in the Docker container

---

## Identified attack vectors

30 bypass vectors have been identified and analyzed.

### Direct database access

| # | Vector | Protection | Status |
|---|--------|-----------|--------|
| 1 | `docker exec mcp_postgres psql` | Hook blocks docker exec on mcp_* containers | Resolved |
| 2 | `psql -h localhost -p 5432` | Ports not exposed if databases are configured without port mapping | Resolved |
| 3 | `mongosh localhost:27017` | Ports not exposed if databases are configured without port mapping | Resolved |
| 4 | `pg_dump` via docker exec | Hook blocks docker exec | Resolved |
| 5 | `docker cp` from the DB container | Hook blocks docker cp | Resolved |
| 6 | `pip install pgcli` + connection | No credentials + no network route | Resolved |

### Credential exfiltration

| # | Vector | Protection | Status |
|---|--------|-----------|--------|
| 7 | Read `~/.mcp-db-results-anonymizer/.env` | Hook + credentials inside the container | Resolved |
| 8 | Read `config.yaml` | No credentials (env vars resolved inside the container) | Partial |
| 9 | Read `docker-compose.yml` | No plaintext credentials, only the path to .env | Partial |
| 10 | `docker inspect mcp_postgres` | Hook blocks docker inspect | Resolved |
| 11 | `docker compose config` | Hook blocks docker compose config | Resolved |
| 12 | `/proc/$(pgrep)/environ` | Hook + MCP inside the container | Resolved |
| 13 | `env` / `printenv` | Credentials not in the host shell | Resolved |

### Access via code and files

| # | Vector | Protection | Status |
|---|--------|-----------|--------|
| 14 | Python script `import psycopg2` | Hook + no credentials on the host | Resolved |
| 15 | `uv run python -c "..."` | No credentials + no network route | Resolved |
| 16 | Read data volumes | Volumes managed by the DB docker-compose (separate) | Resolved |
| 17 | Read MongoDB volumes | Volumes managed by the DB docker-compose (separate) | Resolved |
| 18 | Modify `config.yaml` to disable anonymization | config.yaml mounted as `:ro` | Resolved |
| 19 | Modify source code | Code baked into the Docker image, not accessible | Resolved |

### MCP tool abuse

| # | Vector | Protection | Status |
|---|--------|-----------|--------|
| 20 | SQL alias (`SELECT email AS x`) | SQL lineage tracing (sqlglot) | Resolved |
| 21 | Encoding (`encode(email::bytea, 'hex')`) | `encode`/`convert_from`/`convert_to` blocked | Resolved |
| 22 | Extraction via error messages | Error scanning (partial) | Mitigated |
| 23 | Concatenation (`first_name || email`) | SQL lineage tracing | Resolved |

### Cloud vectors

| # | Vector | Protection | Status |
|---|--------|-----------|--------|
| 24 | Read service account file (BigQuery) | Credentials inside the container only | Resolved |
| 25 | `gcloud auth print-access-token` | Hook + no GCP config on the host | Resolved |
| 26 | `~/.snowflake/connections.toml` | Credentials inside the container only | Resolved |
| 27 | AWS env vars | Credentials inside the container only | Resolved |

### Advanced vectors

| # | Vector | Protection | Status |
|---|--------|-----------|--------|
| 28 | Modify `docker-compose.yml` to expose a port | Hook + requires `docker compose up` | Mitigated |
| 29 | `strace -p <pid>` on the MCP process | Hook blocks strace/ltrace | Resolved |
| 30 | `docker logs` | Hook blocks docker logs | Resolved |

**Summary**: 27/30 vectors resolved, 3 mitigated. This list is not exhaustive : other vectors not yet identified may exist.

---

## Known limitations

### Network security

- **No native TLS**: the MCP server communicates over HTTP. For HTTPS,
  place a reverse proxy in front (see `REVERSE_PROXY_HELP.md`)
- **No RBAC**: all authenticated clients have the same access level.
  No per-user distinction or granular per-database permissions
- **No per-user audit**: logs trace queries but not the caller's
  identity (a single shared Bearer token)

### PII detection

- **Aliased person names**: `SELECT first_name AS f` - SQL lineage
  traces back to the source column, but if the value scan doesn't recognize
  the value as a name (e.g., uncommon first name), it passes through in cleartext
- **Computed MongoDB fields**: a `$group` with alias produces fields
  absent from the original schema → not anonymized
- **PII in the query itself**: `WHERE email = 'jean@example.com'` -
  the email transits in the query. Out of scope for the pipeline
- **Over-anonymization possible**: non-PII columns whose name matches
  a pattern (e.g., `kanban_columns.name` → detected as PERSON) are incorrectly
  anonymized. Solvable via overrides in `config.yaml`

### Architecture

- **Bypassable hooks**: Claude Code hooks intercept Bash commands,
  but the agent can rephrase (`bash -c "ps\ql"`, heredocs, base64...).
  Hooks are a safety net, not a wall - network isolation is the real protection
- **Docker socket dependency**: in secured mode, security relies on
  the agent not having access to the Docker socket. If the socket is mounted
  or if the agent has Docker permissions, all isolation falls apart
