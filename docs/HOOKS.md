# Security Hooks

In addition to network isolation (the **wall**), coding-agent hooks add a **safety net**. They block commands the agent might attempt to bypass the MCP. Claude Code, Codex, OpenCode and Cursor are supported.

## Installation

The installer wires the hook when you connect an agent (main menu → **Installation / Agent setup**). For Claude Code you can also install it directly:

```bash
# Claude Code user settings (~/.claude/settings.json)
make install-hooks

# Restart the agent to activate the hooks
```

Hooks are installed at the user level so they run without permission prompts.

## Blocked Vectors

| Vector | Examples |
|---|---|
| Direct DB clients | `psql`, `mongosh`, `mysql`, `sqlcmd`, `pgcli` |
| Docker exec/inspect/logs | `docker exec mcp_postgres psql`, `docker inspect`, `docker logs` |
| Credential reading | `cat ~/.mcp-db-results-anonymizer/.env`, `/proc/*/environ` |
| Process tracing | `strace`, `ltrace` |
| Exfiltration scripts | `python -c "import psycopg2..."`, `python3 /tmp/script.py` |
| Hook modification | Writing to `scripts/security-hook*.sh` |

## Per-agent wiring

All four agents share the same blocking rules (`scripts/security-hook.sh`); only the integration point differs:

| Agent | Mechanism | Location (global / local) |
|---|---|---|
| Claude Code | `PreToolUse` hooks | `~/.claude/settings.json` / `.claude/settings.json` |
| Codex | `[[hooks.PreToolUse]]` (TOML) | `~/.codex/config.toml` / `.codex/config.toml` |
| OpenCode | JS plugin (`tool.execute.before`) | `~/.config/opencode/plugins/` / `.opencode/plugins/` |
| Cursor | `preToolUse` (JSON) | `~/.cursor/hooks.json` / `.cursor/hooks.json` |

### Claude Code hooks

| Hook | Event | Role |
|---|---|---|
| `security-hook.sh` | `PreToolUse` (Bash) | Blocks dangerous commands |
| `security-hook-read.sh` | `PreToolUse` (Read) | Blocks reading `.env` and `/proc/*/environ` |
| `security-hook-write.sh` | `PreToolUse` (Edit, Write) | Blocks modification of hooks and `.env` |

### Codex, OpenCode and Cursor

All three route the shell command to `security-hook.sh`:
- **Codex** calls it directly via `[[hooks.PreToolUse]]` in `config.toml`
- **OpenCode** delegates via `spawnSync` in a JS plugin (`tool.execute.before`)
- **Cursor** calls it via `preToolUse` in `hooks.json` with `matcher: "Shell"` and `failClosed: true`

Since these agents read and write files through the shell, the single shell hook also covers the `.env` read and hook-modification vectors - no separate read/write hooks are needed.

> The shell tool name (`Bash` for Codex, `bash` for OpenCode, `Shell` for Cursor) follows each SDK's convention. If a build names it differently, adjust the `matcher` in `config.toml` (Codex), `hooks.json` (Cursor), or the `input.tool` check in the plugin (OpenCode).

## Limitations

Hooks are a **best-effort** safety net. Network isolation in secured mode is the real wall - even without hooks, the agent cannot reach the databases because ports are not exposed.

See [SECURITY.md](SECURITY.md) for the full attack vector analysis.
