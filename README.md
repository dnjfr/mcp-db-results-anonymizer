# MCP DB Results Anonymizer

<p align="center">
<a href="README.md"><img src="https://img.shields.io/badge/English-green.svg" /></a>
<a href="README.fr.md"><img src="https://img.shields.io/badge/French-blue.svg" /></a>
</p>

<p align="center">
<img src="https://img.shields.io/badge/Docker-2CA5E0?style=for-the-badge&logo=docker&logoColor=white" />
<img src="https://img.shields.io/badge/Python-FFD43B?style=for-the-badge&logo=python&logoColor=blue" />
<img src="https://img.shields.io/badge/SQLite-%2307405e.svg?style=for-the-badge&logo=sqlite&logoColor=white" />
</p>

Do AI agents really need access to your real data to help you debug a database or code?

MCP DB Results Anonymizer is a Python MCP Server that acts as an **anonymizing proxy** between an AI agent (Claude, GPT, etc.) and databases (PostgreSQL, MySQL, SQL Server + MongoDB).

**The agent never sees real data or connection credentials.**

```
┌──────────────┐                                                        
│              │         ┌─────────────────────────────────────────────┐
│   AI Agent   │         │                                             │     ┌──────────────┐
│  (Claude,    │         │          MCP-DB-RESULTS-ANONYMIZER          │┌───►│  PostgreSQL  │
│   Codex...)  │         │                                             ││    └──────────────┘
│              │         │  1. Receives the agent's query              ││    ┌──────────────┐
│  NEVER sees  │         │  2. Executes the query on the DB            │├───►│    MySQL     │
│  the real    │         │  3. Detects PII columns (schema+regex)      ││    └──────────────┘
│  data        │◄──MCP──►│  4. Pseudonymizes (Faker + seed hash)       ││    ┌──────────────┐
│              │         │  5. Returns faked data                      │├───►│  SQL Server  │
│  NEVER knows │         │                                             ││    └──────────────┘
│  the         │         │  Credentials stored server-side             ││    ┌──────────────┐
│  credentials │         │  Consistent mapping in local SQLite         │└───►│   MongoDB    │
│              │         │                                             │     └──────────────┘
│              │         └─────────────────────────────────────────────┘
└──────────────┘                                │
                                                ▼
                                        ┌──────────────┐
                                        │ Local SQLite │
                                        │ (hashed      │
                                        │  mappings)   │
                                        └──────────────┘
```

## Why?

When an AI agent queries a database, **the entire result passes through the AI provider's servers** - names, emails, phone numbers, addresses, connection credentials.

No existing solution addresses this problem end-to-end via the MCP protocol. Alternatives (mcp-presidio, anonymize.dev) anonymize *after* the agent has already seen the data. Here, the agent has **no direct access** to the DB: it must go through the MCP server, which anonymizes before returning the results.

The MCP is the **wall** (security by design). The project also provides coding-agent hooks (Claude Code, Codex, OpenCode, Cursor) as an additional **safety net** - see [docs/SECURITY.md](docs/SECURITY.md).

### The flow from the agent's perspective

1. The agent sends the SQL query to the MCP proxy
2. The proxy executes the query on the real database
3. The proxy detects columns containing personal data (PII) - see [docs/DETECTION.md](docs/DETECTION.md)
4. It replaces real values with pseudonyms
5. It returns only the anonymized result to the agent

**The agent never sees the real data.** Real names, real salaries, etc. never leave the proxy.

**Why send results to the agent at all?** Because its role is to help analyze and interpret results - spotting duplicates, anomalies, reformulating data. For that, it needs to see the structure and content of the result. Without the data, it could only fix SQL syntax without ever verifying that the result matches what the user is looking for.

The anonymizer is the compromise: the agent receives data that is **realistic in structure** (correct columns, correct row count, correct types) but **fake in content** for anything sensitive. This lets it help concretely without exposing personal data.

## Designed for / Not designed for

### What the MCP IS designed for

| Use case | Why it works |
|---|---|
| **Debugging a failing query** | The agent fixes your SQL syntax, joins, missing columns - the anonymized result proves the fix works |
| **Checking data quality** | NULL values, NaN, empty columns, data types, duplicates - these are preserved as-is through anonymization |
| **Understanding schema & relationships** | `describeTable` returns real column names, types, and foreign keys |
| **Generating test fixtures** | `generateTestFixtures` exports realistic but fully fake data |
| **Validating query logic** | Row counts, GROUP BY structure, JOIN cardinality - all preserved |

### What the MCP is NOT designed for

| Anti-pattern | Why it doesn't work |
|---|---|
| **Analyzing data values after anonymization** | Names, emails, addresses are replaced with fake equivalents - "Which city do most customers come from?" is unanswerable |
| **Statistical analysis on anonymized results** | Salaries are perturbed ±15%, dates are shifted - averages, trends, and distributions won't match reality |
| **Comparing specific values across queries** | Pseudonymization is deterministic *within a session*, but the values are still fake - don't draw business conclusions from them |
| **Auditing real data** | By design, the agent never sees real PII - if you need to verify actual values, query the database directly |

> **In short**: the MCP answers *"why does this query fail?"* and *"is the data structure correct?"*, not *"what does the data say?"*.

## Architecture

The MCP server is published on **Docker Hub**: [`dnjfr/mcp-db-results-anonymizer`](https://hub.docker.com/r/dnjfr/mcp-db-results-anonymizer).

The interactive installer creates a standalone infrastructure directory that pulls the image from Docker Hub - no local build required.

The MCP server connects to databases via a shared Docker network (`mcp-db-results-anonymizer-network`). Databases are managed separately - the MCP does not bundle them.

**Prerequisites**: Docker + Docker Compose.

## Installation

```bash
git clone https://github.com/dnjfr/mcp-db-results-anonymizer.git
cd mcp-db-results-anonymizer
bash install.sh
```

The installer creates a directory **alongside** the project, separate from the source code:

```
projects/
├── mcp-db-results-anonymizer/      ← source code (this repo)
└── db-results-anonymizer-infra/    ← created by the installer
```

It guides you step by step:
1. Choose the directory name (default: `db-results-anonymizer-infra`)
2. Configure credentials (auto-generation available)
3. Start the infrastructure (PostgreSQL + MongoDB + MCP server)
4. Choose which demo databases to import
5. Connect your coding agent (Claude Code, Codex, OpenCode, or Cursor) - MCP registration + security hooks are installed automatically

<details>
<summary>Available demo databases</summary>

All demo databases are prefixed with `demo_` to avoid conflicts with your own databases.

| # | Database | Content |
|---|---|---|
| 1 | demo_pagila | Video rental - 600 customers, addresses, payments (PostgreSQL) |
| 2 | demo_chinook | Music store - 60 customers, emails, invoices (PostgreSQL) |
| 3 | demo_employees | HR - 300k employees, salaries, dates of birth (PostgreSQL) |
| 4 | demo_adventureworks | E-commerce/HR - 20k people, emails, phones (PostgreSQL) |
| 5 | demo_ecommerce | 10,000 fake customers (MongoDB) |

To uninstall all demos: `make uninstall-demos`

</details>

<details>
<summary>Port conflicts</summary>

The installer exposes default host ports. If you already have services running on these ports, edit `docker-compose.yml` in the infrastructure directory after installation:

| Service | Default | Change to |
|---|---|---|
| PostgreSQL | `5434:5432` | `5435:5432` (or any free port) |
| MongoDB | `27017:27017` | `27018:27017` |
| MySQL | `3306:3306` | `3307:3306` |
| SQL Server | `1433:1433` | `1434:1433` |

These ports are only used for **host access** (e.g. connecting with a SQL client). The MCP server connects to databases via the internal Docker network and is not affected by port changes.

</details>

<details>
<summary>Connect your own existing databases</summary>

If you already have test databases, they must join the Docker network `mcp-db-results-anonymizer-network`:

```yaml
# Your docker-compose.yml
services:
  postgres:
    image: postgres:16
    container_name: mcp_postgres
    networks:
      - mcp-db-results-anonymizer-network

networks:
  mcp-db-results-anonymizer-network:
    external: true
```

Then configure the connection in `~/.mcp-db-results-anonymizer/config.yaml` (see [docs/CONFIGURATION.md](docs/CONFIGURATION.md)):

```yaml
databases:
  my_database:
    type: postgresql
    host: mcp_postgres          # container name
    port: 5432
    user: ${POSTGRES_USER}
    password: ${POSTGRES_PASSWORD}
    database: my_database
```

</details>

<details>
<summary>Difference between global and local</summary>

| | `setup-global` | `setup-local` |
|---|---|---|
| Registration | `~/.claude.json` (user scope) | `.mcp.json` (project scope) |
| Scope | All projects | This directory only |

</details>

<details>
<summary>Supported coding agents</summary>

Because this is a standard MCP server, any MCP-capable agent can connect. The installer's **Installation / Agent setup** menu (option 1) wires each one up - MCP registration **and** its security hook, with a global or local scope choice:

| Agent | Endpoint | Config (global / local) | Security hook |
|---|---|---|---|
| Claude Code | `/sse` | `~/.claude.json` / `.mcp.json` | `PreToolUse` in `settings.json` |
| Codex | `/mcp` (streamable-http) | `~/.codex/config.toml` / `.codex/config.toml` | `[[hooks.PreToolUse]]` in `config.toml` |
| OpenCode | `/sse` | `~/.config/opencode/opencode.json` / `opencode.json` | JS plugin (`tool.execute.before`) |
| Cursor | `/sse` | `~/.cursor/mcp.json` / `.cursor/mcp.json` | `preToolUse` in `hooks.json` |

The server exposes `/sse` and `/mcp` in parallel, so every agent connects to the same instance. All four share the same blocking rules (`scripts/security-hook.sh`). See [HOOKS.md](docs/HOOKS.md).

</details>

## Usage Example

A user asks their AI agent to fix a query on `demo_employees`. The query uses the well-known MySQL column names (`emp_no`, `salary`, `salaries`), but this database uses a **different PostgreSQL schema** (`id`, `amount`, `salary`) - the agent can't guess, it has to look:

```sql
I wrote a query on demo_employees but it crashes.
Can you explain why and fix it?

SELECT e.emp_no, e.first_name, e.last_name, s.salary, t.title
FROM employees e
JOIN salaries s ON e.emp_no = s.emp_no
JOIN titles t ON e.emp_no = t.emp_no
WHERE e.hire_date > '1995-01-01'
  AND t.to_date = '9999-01-01'
  AND s.to_date = '9999-01-01'
ORDER BY s.salary DESC
LIMIT 5;
```

The agent tries to run it via the MCP. It fails, so it investigates:

```
I'll check the real schema of demo_employees to identify the errors in your query.

Let me check the schema of the three tables used in your query.

● Called mcp-db-results-anonymizer 3 times

Your query contains 5 errors related to the actual database schema. Here are the details:

┌─────┬───────────────────────────┬──────────────────────────────────┬───────────────────────────────────────────────────────────────────────────────┐
│  #  │  Error in your query      │         Correct value            │                                  Explanation                                  │
├─────┼───────────────────────────┼──────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────┤
│ 1   │ FROM employees e          │ FROM employees.employee e        │ The table is employees.employee (schema employees, table employee)            │
├─────┼───────────────────────────┼──────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────┤
│ 2   │ JOIN salaries s           │ JOIN employees.salary s          │ The table is employees.salary (not salaries)                                  │
├─────┼───────────────────────────┼──────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────┤
│ 3   │ JOIN titles t             │ JOIN employees.title t           │ The table is employees.title (not titles)                                     │
├─────┼───────────────────────────┼──────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────┤
│ 4   │ e.emp_no / s.emp_no /     │ e.id / s.employee_id /           │ The primary key of employee is id, and the foreign key in salary and title    │
│     │ t.emp_no                  │ t.employee_id                    │ is employee_id                                                                │
├─────┼───────────────────────────┼──────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────┤
│ 5   │ s.salary                  │ s.amount                         │ The salary column is called amount, not salary                                │
└─────┴───────────────────────────┴──────────────────────────────────┴───────────────────────────────────────────────────────────────────────────────┘

Here is the corrected query:
```

```sql
SELECT e.id, e.first_name, e.last_name, s.amount, t.title
FROM employees.employee e
JOIN employees.salary s ON e.id = s.employee_id
JOIN employees.title t ON e.id = t.employee_id
WHERE e.hire_date > '1995-01-01'
  AND t.to_date = '9999-01-01'
  AND s.to_date = '9999-01-01'
ORDER BY s.amount DESC
LIMIT 5;

Do you want me to run it to verify it works?

```

If the user accepts:

```
● Called mcp-db-results-anonymizer

The query works, here are the 5 results:

┌────────┬────────────┬───────────┬─────────┬──────────────┐
│   id   │ first_name │ last_name │ amount  │    title     │
├────────┼────────────┼───────────┼─────────┼──────────────┤
│ 77152  │ Josette    │ Techer    │ 147 777 │ Senior Staff │
├────────┼────────────┼───────────┼─────────┼──────────────┤
│ 96407  │ Lorraine   │ Vaillant  │ 137 374 │ Senior Staff │
├────────┼────────────┼───────────┼─────────┼──────────────┤
│ 458302 │ Camille    │ Fontaine  │ 143 984 │ Staff        │
├────────┼────────────┼───────────┼─────────┼──────────────┤
│ 298341 │ Michelle   │ Dupuy     │ 137 960 │ Staff        │
├────────┼────────────┼───────────┼─────────┼──────────────┤
│ 276755 │ Daniel     │ Merle     │ 111 490 │ Senior Staff │
└────────┴────────────┴───────────┴─────────┴──────────────┘

Warning: Names and monetary amounts are pseudonymized by the database anonymization proxy. 
These are not the actual data-the IDs are reliable, but PII values (such as names and salaries) are 
replaced with realistic fake data on every query.
```

The agent returns the **corrected, working query** to the user, and it never saw the real PII.

> **Note**: the MCP proxy only intercepts queries that are actually executed. An AI agent that knows the schema can fix a broken query without ever running it, in which case the MCP does not intervene because no data flows through it. This is not a security issue (no data = nothing to protect), but it means the agent won't verify that the result is correct. To have it validate the result, explicitly ask: "run the corrected query to verify".

## MCP Tools

| Tool | Description |
|---|---|
| `listTables` | Lists tables (SQL) or collections (NoSQL) of a database |
| `describeTable` | Describes the schema + automatically detects PII columns |
| `querySql` | Executes a SELECT and returns anonymized results |
| `queryNosql` | Executes an anonymized MongoDB query (`find`) |
| `queryNosqlAggregate` | Executes an anonymized MongoDB aggregation pipeline |
| `generateTestFixtures` | Exports N anonymized rows/documents as JSON or CSV |

## Features

- **4 databases supported**: PostgreSQL, MySQL, SQL Server, MongoDB (see [docs/CONFIGURATION.md](docs/CONFIGURATION.md))
- **[5-layer PII detection](docs/DETECTION.md)**: manual classification, column name patterns, value regex, SQL lineage via sqlglot, value scan fallback
- **Deterministic pseudonymization**: Faker with seed based on `hash(value + session_salt)`
- **Read-only SQL validation**: multi-dialect, fail-closed
- **Defense in depth**: isolated container + hooks + Bearer token + rate limiting
- **Lightweight stack**: no ML, no Presidio - pattern matching + Faker + sqlglot

## Project Structure

```
mcp-db-results-anonymizer/          ← source code (Docker Hub: dnjfr/mcp-db-results-anonymizer)
├── src/
│   ├── server.py                   # MCP entry point (FastMCP)
│   ├── config.py                   # YAML config loader
│   ├── database/                   # SQL + MongoDB connectors
│   ├── detection/                  # Pattern matching, SQL lineage, value scan
│   ├── anonymizer/                 # Faker pseudonymization + numeric perturbation
│   ├── storage/                    # Local SQLite (hashed mappings)
│   ├── security/                   # SQL validation, Bearer auth, rate limiting
│   └── tools/                      # MCP tools (query, metadata, fixtures)
├── scripts/                        # Shared security hooks (all agents)
├── infra/                          # Templates for the installer
├── docs/                           # Detailed documentation
├── install.sh                      # Interactive installer
├── config.example.yaml             # Configuration template
├── Dockerfile                      # MCP server image
└── Makefile                        # Docker Hub build/push + MCP registration
```

## Known Limitations

- **Person names**: if an uncommon first name escapes the value scan, it passes through in plain text
- **MongoDB aggregate**: computed fields absent from the original schema are not anonymized
- **PII in the query**: `WHERE email = 'jean@example.com'` - the email is transmitted in the query itself
- **No native TLS**: place a reverse proxy in front for HTTPS (see [docs/REVERSE_PROXY_HELP.md](docs/REVERSE_PROXY_HELP.md))

## Documentation

| Document | Content |
|---|---|
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Operating modes, config.yaml, supported databases, mapping, authentication |
| [docs/DETECTION.md](docs/DETECTION.md) | Detected PII types, detection pipeline, manual classification |
| [docs/HOOKS.md](docs/HOOKS.md) | Security hooks (Claude Code, Codex, OpenCode, Cursor), blocked vectors |
| [docs/SECURITY.md](docs/SECURITY.md) | Threat model, 30 attack vectors, protections |
| [docs/REVERSE_PROXY_HELP.md](docs/REVERSE_PROXY_HELP.md) | nginx + TLS network deployment |

## License

Apache License 2.0
