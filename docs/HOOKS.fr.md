# Hooks de sécurité

En complément de l'isolation réseau (le **mur**), des hooks d'agent de code ajoutent un **filet de sécurité**. Ils bloquent les commandes que l'agent pourrait tenter pour contourner le MCP. Claude Code, Codex, OpenCode et Cursor sont supportés.

## Installation

L'installeur configure le hook lorsque vous connectez un agent (menu principal → **Installation / Agent setup**). Pour Claude Code, vous pouvez aussi l'installer directement :

```bash
# Settings utilisateur Claude Code (~/.claude/settings.json)
make install-hooks

# Redémarrez l'agent pour activer les hooks
```

Les hooks sont installés au niveau utilisateur pour s'exécuter sans demande de permission.

## Vecteurs bloqués

| Vecteur | Exemples |
|---|---|
| Clients DB directs | `psql`, `mongosh`, `mysql`, `sqlcmd`, `pgcli` |
| Docker exec/inspect/logs | `docker exec mcp_postgres psql`, `docker inspect`, `docker logs` |
| Lecture credentials | `cat ~/.mcp-db-results-anonymizer/.env`, `/proc/*/environ` |
| Traçage de processus | `strace`, `ltrace` |
| Scripts d'exfiltration | `python -c "import psycopg2..."`, `python3 /tmp/script.py` |
| Modification des hooks | Écriture dans `scripts/security-hook*.sh` |

## Configuration par agent

Les quatre agents partagent les mêmes règles de blocage (`scripts/security-hook.sh`) ; seul le point d'intégration diffère :

| Agent | Mécanisme | Emplacement (global / local) |
|---|---|---|
| Claude Code | Hooks `PreToolUse` | `~/.claude/settings.json` / `.claude/settings.json` |
| Codex | `[[hooks.PreToolUse]]` (TOML) | `~/.codex/config.toml` / `.codex/config.toml` |
| OpenCode | Plugin JS (`tool.execute.before`) | `~/.config/opencode/plugins/` / `.opencode/plugins/` |
| Cursor | `preToolUse` (JSON) | `~/.cursor/hooks.json` / `.cursor/hooks.json` |

### Hooks Claude Code

| Hook | Événement | Rôle |
|---|---|---|
| `security-hook.sh` | `PreToolUse` (Bash) | Bloque les commandes dangereuses |
| `security-hook-read.sh` | `PreToolUse` (Read) | Bloque la lecture du `.env` et `/proc/*/environ` |
| `security-hook-write.sh` | `PreToolUse` (Edit, Write) | Bloque la modification des hooks et du `.env` |

### Codex, OpenCode et Cursor

Les trois routent la commande shell vers `security-hook.sh` :
- **Codex** l'appelle directement via `[[hooks.PreToolUse]]` dans `config.toml`
- **OpenCode** y délègue via `spawnSync` dans un plugin JS (`tool.execute.before`)
- **Cursor** l'appelle via `preToolUse` dans `hooks.json` avec `matcher: "Shell"` et `failClosed: true`

Comme ces agents lisent et écrivent les fichiers via le shell, l'unique hook shell couvre aussi les vecteurs de lecture du `.env` et de modification des hooks - pas besoin de hooks read/write séparés.

> Le nom de l'outil shell (`Bash` pour Codex, `bash` pour OpenCode, `Shell` pour Cursor) suit la convention de chaque SDK. Si un build le nomme différemment, ajustez le `matcher` dans `config.toml` (Codex), `hooks.json` (Cursor), ou le test `input.tool` dans le plugin (OpenCode).

## Limites

Les hooks sont un filet de sécurité **best-effort**. L'isolation réseau du mode sécurisé est le vrai mur - même sans les hooks, l'agent ne peut pas atteindre les bases de données car les ports ne sont pas exposés.

Voir [SECURITY.md](SECURITY.md) pour l'analyse complète des vecteurs d'attaque.
