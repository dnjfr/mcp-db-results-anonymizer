# Configuration

## Deux modes de fonctionnement

Le projet propose deux modes : un mode **sécurisé** (par défaut) et un mode **dev**.

### Mode sécurisé (recommandé)

```bash
make start-secured
```

Le MCP server tourne dans un conteneur Docker et communique via SSE (HTTP). Il se connecte à vos bases de données via le réseau Docker partagé `mcp-db-results-anonymizer-network`.

```
Agent IA ──── SSE (port 8080) ──── Conteneur MCP ──── mcp-db-results-anonymizer-network ──── Vos bases de données
(extérieur)                        (anonymise)         (partagé)        (conteneurs séparés)
```

Deux modes d'enregistrement du MCP sont disponibles :

#### Global (recommandé)

```bash
make setup-global
# ou séparément :
make install-mcp-global
```

Enregistre le MCP dans `~/.claude.json` (scope `user`) via `claude mcp add --scope user`. Le MCP sera visible dans chaque session Claude Code, quel que soit le répertoire de travail.

#### Local

```bash
make setup-local
# ou séparément :
make install-mcp-local
```

Génère un `.mcp.json` dans le répertoire du projet. Le MCP n'est accessible que depuis ce répertoire. Le mode de transport (SSE ou stdio) est détecté automatiquement selon que le conteneur MCP tourne ou non.

> **Note** : le `.mcp.json` peut contenir la clé API en clair (imposé par le format des clients MCP). C'est pourquoi il est dans le `.gitignore` et généré dynamiquement depuis le `.env`.

### Mode dev

```bash
make start-dev
```

Pour le développement du projet : le MCP tourne en local via `uv run` (stdio), sans Docker. Les bases de données doivent être accessibles depuis localhost (ports exposés ou bases locales).

```bash
# Arrêter / supprimer le conteneur MCP :
make stop           # Arrête sans supprimer (redémarrage rapide)
make down           # Supprime le conteneur MCP
```

Après chaque changement, reconnectez le MCP dans votre agent (dans Claude Code : `/mcp`).

## Connecter vos bases de données

Le MCP est **standalone** : il ne gère pas les conteneurs de bases de données. Vos bases doivent être accessibles sur le réseau Docker `mcp-db-results-anonymizer-network` (mode sécurisé) ou via localhost (mode dev).

```bash
# Créer le réseau partagé (une seule fois)
docker network create mcp-db-results-anonymizer-network

# Vos conteneurs DB doivent rejoindre ce réseau
# Exemple dans votre docker-compose.yml :
#   networks:
#     mcp-db-results-anonymizer-network:
#       external: true
```

## config.yaml

Le fichier de configuration est externalisé dans `~/.mcp-db-results-anonymizer/config.yaml` (au même endroit que le `.env`). Un template est fourni dans le repo sous `config.example.yaml`.

```bash
cp config.example.yaml ~/.mcp-db-results-anonymizer/config.yaml
```

Ce fichier contrôle :
- Les connexions aux bases de données (credentials via `${VARIABLES_ENVIRONNEMENT}`)
- Les patterns de détection PII et business (extensibles)
- La classification de sensibilité manuelle (optionnelle)
- Les overrides par colonne (forcer ou exclure)
- Les paramètres de sécurité (tables bloquées, fonctions bloquées, max rows)
- La locale Faker pour la pseudonymisation (`fr_FR` par défaut)

### Exemple de connexion

```yaml
databases:
  ma_base:
    type: postgresql
    host: mcp_postgres          # nom du conteneur sur mcp-db-results-anonymizer-network
    port: 5432                  # port interne Docker
    user: ${POSTGRES_USER}
    password: ${POSTGRES_PASSWORD}
    database: ma_base
```

Les hosts utilisent le **nom du conteneur** Docker (résolution DNS via `mcp-db-results-anonymizer-network`). En mode dev (stdio), utilisez `localhost` et le port exposé.

## Bases de données supportées

| Base | Driver | Read-only | Dépendance |
|---|---|---|---|
| PostgreSQL | psycopg2-binary (SQLAlchemy) | `SET default_transaction_read_only = ON` | Inclus par défaut |
| MySQL | pymysql (SQLAlchemy) | `SET SESSION TRANSACTION READ ONLY` | `uv sync --extra mysql` |
| SQL Server | pyodbc (SQLAlchemy) | `pyodbc.readonly = True` | `uv sync --extra mssql` |
| MongoDB | pymongo | Pas d'opération d'écriture dans le code | Inclus par défaut |

> **Extensibilité** : l'architecture permettrait d'ajouter des bases cloud (Snowflake, BigQuery, Atlas) mais aucun connecteur cloud n'est implémenté à ce jour. Les credentials cloud resteraient isolés dans le conteneur MCP.

## Modes de mapping

### Mode `ephemeral` (par défaut)

Les mappings sont **purgés après chaque requête**. Les pseudonymes sont cohérents au sein d'une requête mais pas entre deux requêtes successives. Pour croiser des données, utilisez une seule requête avec `JOIN` ou `UNION`.

### Mode `session`

Les mappings sont conservés pour la **cohérence inter-requêtes**. Le même `user_id` produira le même pseudonyme entre deux requêtes.

- `purgeMappings()` est exposé - appelez-le en fin d'analyse
- TTL d'inactivité de 30 minutes par défaut
- Mappings purgés au démarrage du serveur

### Configuration des modes

**Via variables d'environnement** (prioritaires) :

```bash
ANONYMIZER_MODE=session           # ephemeral (défaut) ou session
ANONYMIZER_TTL_MINUTES=60         # TTL en minutes (défaut: 30)
```

**Via `config.yaml`** :

```yaml
storage:
  path: .db-anonymized/mappings.db
  mode: session
  ttl_minutes: 60
```

> **Note** : en mode sécurisé (Docker), ce chemin est résolu à l'intérieur du conteneur (`/app/.db-anonymized/mappings.db`) et la base n'est pas persistée entre les redémarrages. En mode dev, elle est créée relativement au répertoire de travail courant.

> **Recommandation** : restez en mode `ephemeral` sauf si vous avez explicitement besoin de croiser les résultats de plusieurs requêtes.

## Authentification API key (mode sécurisé)

En mode sécurisé, le serveur peut exiger un Bearer token pour chaque connexion.

> **Production** : sans `MCP_API_KEY`, le serveur accepte **toutes les connexions sans authentification**.

```bash
# 1. Générer une clé et l'ajouter au .env (une seule fois)
echo "MCP_API_KEY=$(openssl rand -hex 32)" >> ~/.mcp-db-results-anonymizer/.env

# 2. Relancer
make stop && make start-secured
```

La clé persiste entre les redémarrages.

### Endpoints de transport

En mode sécurisé, le serveur expose **deux transports en parallèle** sur le même port, pour que n'importe quel client MCP fonctionne :

| Transport | Endpoint | Utilisé par |
|---|---|---|
| SSE | `http://localhost:8080/sse` | Claude Code, OpenCode, Cursor |
| Streamable HTTP | `http://localhost:8080/mcp` | Codex |

Les deux sont protégés par le même Bearer token et le même rate limiting. `MCP_TRANSPORT=sse` (par défaut) sert les deux ; seul `MCP_TRANSPORT=stdio` désactive le serveur réseau.

### Reverse proxy TLS

Pour un déploiement réseau, voir **[REVERSE_PROXY_HELP.md](REVERSE_PROXY_HELP.md)** : configuration nginx + Let's Encrypt.
