# Sécurité

Ce document décrit le modèle de menace, les protections implémentées,
les vecteurs d'attaque identifiés et les limitations connues du projet.

## Modèle de menace

Le MCP server est un proxy entre un agent IA et des bases de données.
L'agent est considéré comme **non fiable** : s'il existe un chemin technique
pour accéder aux données brutes, il finira par l'emprunter - même sans intention
malveillante.

> **Constat fondateur** (29/06/2026) : lors d'un test sur une base de production,
> l'agent IA a contourné le MCP en exécutant un `docker exec` direct sur le
> conteneur PostgreSQL. Des données personnelles réelles ont transité par les
> serveurs du fournisseur IA. L'agent l'a fait de sa propre initiative.
> Conclusion : la sécurité doit être **architecturale**, pas comportementale.

## Architecture de sécurité : la frontière de confiance

Le conteneur MCP est la **frontière de confiance**. Tout ce qui est sensible
(credentials, données brutes) reste à l'intérieur. L'agent IA est à l'extérieur
et ne communique que via SSE - il ne voit jamais les données réelles.

```
┌──────────────────────────────────────────────────────────────────┐
│                      EXTÉRIEUR (non fiable)                      │
│                                                                  │
│   Agent IA (Claude, GPT, Cursor...)                              │
│   ├── N'a PAS les credentials                                    │
│   ├── N'a PAS accès aux ports DB (non exposés en mode sécurisé)   │
│   ├── N'a PAS accès au socket Docker (non monté)                 │
│   ├── N'a PAS accès aux volumes de données (gérés séparément)    │
│   └── Communique UNIQUEMENT via SSE (port 8080, auth Bearer)     │
│                                │                                 │
│                           SSE + API key                          │
│                                │                                 │
├────────────────────────────────┼─────────────────────────────────┤
│                        INTÉRIEUR (fiable)                        │
│                                │                                 │
│                                ▼                                 │
│   Conteneur MCP Server                                           │
│   ├── Credentials injectés via env_file (pas exposés sur le host)│
│   ├── Se connecte aux DBs via réseau Docker partagé (mcp-db-results-anonymizer-network)│
│   ├── Reçoit les données BRUTES                                  │
│   ├── Anonymise via le pipeline de détection PII                 │
│   └── Renvoie UNIQUEMENT les données pseudonymisées              │
│                  │                         │                     │
│             ┌────┴──────┐           ┌──────┴──────┐              │
│             │ DB locale │           │ DB cloud    │              │
│             │ (réseau   │           │ (Snowflake, │              │
│             │  Docker   │           │  BigQuery,  │              │
│             │  partagé) │           │  Atlas)     │              │
│             └───────────┘           └─────────────┘              │
└──────────────────────────────────────────────────────────────────┘
```

### Ce que la containerisation résout

| Catégorie de vecteur | Résolu ? | Comment |
|---|---|---|
| Accès direct aux DBs (`docker exec`, `psql`, `mongosh`) | Oui | Pas d'accès au socket Docker + ports non exposés (si configuré ainsi) |
| Exfiltration de credentials (`.env`, `docker inspect`, `/proc/*/environ`) | Oui | Credentials uniquement dans le conteneur MCP |
| Scripts d'exfiltration (`python -c "import psycopg2..."`) | Oui | Pas de credentials + pas de route réseau vers les DBs (réseau Docker isolé) |
| Lecture des volumes de données (`pgdata`, `mongodata`) | Oui | Volumes gérés par le docker-compose des bases (séparé du MCP) |
| Modification du code source ou de la config | Oui | Code dans l'image, `config.yaml` monté en `:ro` |
| Credentials cloud (service accounts, tokens) | Oui | Fichiers et variables d'env uniquement dans le conteneur |

### Ce que la containerisation ne résout PAS

- **Abus des outils MCP** : les alias SQL, l'encodage, la concaténation sont des
  vecteurs qui contournent la détection PII, pas l'architecture. Résolus par le
  code (traçage de lignée sqlglot, blocage de `encode`/`convert_from`), pas par l'infra.
- **PII dans la requête elle-même** : si l'agent écrit
  `WHERE email = 'jean@example.com'`, l'email transite. Hors scope.

### Pourquoi les hooks ne suffisent pas ?

Les hooks d'agent de code interceptent les commandes shell avant exécution.
Ils bloquent `docker exec`, `psql`, `cat .env`, etc. Mais :

- L'agent peut **reformuler** : `bash -c "ps\ql"`, variables, heredocs, base64...
- L'agent peut **écrire un script** Python qui fait la même chose
- Les hooks ne protègent que les agents où ils sont installés (Claude Code,
  Codex, OpenCode, Cursor) - un autre agent (Copilot) utilisant le même MCP
  n'aurait pas ces hooks

Les hooks sont un **filet de sécurité** (ils bloquent les contournements par réflexe),
mais ils ne constituent pas une solution de sécurité. L'isolation réseau est le vrai mur.

## Stratégie de défense en profondeur

```
Couche 1 - Architecture (le mur)
  └── MCP containerisé, credentials isolés, ports non exposés,
      authentification API key, binding localhost

Couche 2 - Hooks (le filet)
  └── Bloquent docker exec, psql, mongosh, lecture .env,
      modification config.yaml, etc.

Couche 3 - Pipeline de détection (le scan)
  └── Pattern matching sur noms de colonnes + traçage de lignée SQL
      (sqlglot) + fallback value scan sur les résultats

Couche 4 - Validation des requêtes (le filtre)
  └── Read-only, multi-statements bloqués, fonctions dangereuses
      bloquées par dialecte, tables système interdites
```

Chaque couche compense les failles des autres :
- Si les hooks sont contournés → le conteneur bloque
- Si un alias SQL esquive la détection par nom → le value scan rattrape
- Si le value scan rate un pattern → les hooks bloquent l'accès direct

---

## Protections implémentées

### Transport et réseau

| Protection | Détail |
|---|---|
| **Binding localhost** | Le serveur écoute sur `127.0.0.1` par défaut (pas `0.0.0.0`). En Docker, l'exposition est limitée à `127.0.0.1:8080` |
| **Authentification API key** | Middleware Bearer token sur les endpoints `/sse` et `/messages/`. Clé configurée via `MCP_API_KEY` dans `.env`. Comparaison timing-safe (`hmac.compare_digest`) |
| **Réseau Docker partagé** | Le MCP rejoint le réseau `mcp-db-results-anonymizer-network` pour atteindre les bases. Les bases ne sont pas dans le même docker-compose - elles sont gérées séparément |
| **Ports DB non exposés** | En mode sécurisé, les bases ne devraient pas exposer de ports sur le host (responsabilité de l'admin) |
| **Reverse proxy TLS** | Documentation fournie pour nginx + Let's Encrypt (`REVERSE_PROXY_HELP.md`) |

### Authentification API key

En mode SSE (Docker), le serveur peut exiger un Bearer token :

```bash
# Générer une clé (ASCII pur, obligatoire - les accents ne sont pas supportés dans les headers HTTP)
echo "MCP_API_KEY=$(openssl rand -hex 32)" >> ~/.mcp-db-results-anonymizer/.env
```

Comportement :
- `MCP_API_KEY` définie → toute requête sans `Authorization: Bearer <clé>` est rejetée (401/403)
- `MCP_API_KEY` non définie → warning dans les logs, connexions acceptées (rétrocompatibilité)
- En mode stdio (local) → pas de vérification (pas de réseau)

La commande `make install-mcp-local` injecte automatiquement le header dans `.mcp.json`.

### Logging et audit

Chaque appel d'outil MCP est tracé dans les logs :

```
2026-06-30 09:15:23 | mcp.server | INFO | querySql | database=pagila | query=SELECT * FROM customer...
2026-06-30 09:15:23 | mcp.server | INFO | querySql | database=pagila | 42 lignes | 85ms
```

Les tentatives d'authentification rejetées sont aussi loguées avec l'adresse IP source.

### Validation des requêtes

| Protection | Dialectes | Détail |
|---|---|---|
| **Read-only garanti par le moteur DB** | PG, MySQL, MSSQL | Transaction read-only au niveau driver |
| **Mots-clés d'écriture bloqués** | Tous | INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, COPY, CREATE, GRANT, REVOKE |
| **Multi-statements bloqués** | Tous | Le `;` interdit sauf en fin de requête |
| **Fonctions dangereuses** | PG | `pg_read_file`, `dblink`, `lo_import`, `encode`, `convert_from` |
| | MySQL | `LOAD_FILE`, `INTO OUTFILE`, `BENCHMARK`, `SLEEP` |
| | MSSQL | `xp_cmdshell`, `OPENROWSET`, `sp_configure` |
| **Tables système** | PG | `pg_shadow`, `pg_authid`, `pg_roles`, `information_schema` |
| | MySQL | `mysql.user`, `mysql.db`, `performance_schema` |
| | MSSQL | `sys.sql_logins`, `sys.server_principals`, `sys.credentials` |
| **Identifiants validés** | Tous | `validate_identifier()` - alphanumériques + underscore uniquement |
| **Opérateurs NoSQL dangereux** | MongoDB | `$where`, `$function`, `$accumulator`, `$expr` bloqués |
| **Stages d'écriture NoSQL** | MongoDB | `$out`, `$merge` bloqués dans les pipelines d'agrégation |

### Pipeline d'anonymisation

5 couches de détection (classification manuelle, pattern matching, value scan, lignée SQL, fallback value scan). Fail-closed : si la détection échoue, la requête est bloquée.

Détail complet dans [DETECTION.fr.md](DETECTION.fr.md).

### Autres protections

- **Credentials isolés** : `.env` dans `~/.mcp-db-results-anonymizer/`, hors du projet
- **Mapping hashé** : valeurs réelles hashées SHA-256 en SQLite (irréversible)
- **Nettoyage auto** : mappings de session vidés à l'arrêt du serveur
- **Messages d'erreur nettoyés** : emails, téléphones, IPs, patterns `Key=(value)` masqués
- **config.yaml en read-only** : monté `:ro` dans le conteneur Docker

---

## Vecteurs d'attaque identifiés

30 vecteurs de contournement ont été identifiés et analysés.

### Accès direct aux bases de données

| # | Vecteur | Protection | Statut |
|---|---------|-----------|--------|
| 1 | `docker exec mcp_postgres psql` | Hook bloque docker exec sur les conteneurs mcp_* | Résolu |
| 2 | `psql -h localhost -p 5432` | Ports non exposés si les bases sont configurées sans port mapping | Résolu |
| 3 | `mongosh localhost:27017` | Ports non exposés si les bases sont configurées sans port mapping | Résolu |
| 4 | `pg_dump` via docker exec | Hook bloque docker exec | Résolu |
| 5 | `docker cp` depuis le conteneur DB | Hook bloque docker cp | Résolu |
| 6 | `pip install pgcli` + connexion | Pas de credentials + pas de route réseau | Résolu |

### Exfiltration des credentials

| # | Vecteur | Protection | Statut |
|---|---------|-----------|--------|
| 7 | Lire `~/.mcp-db-results-anonymizer/.env` | Hook + credentials dans le conteneur | Résolu |
| 8 | Lire `config.yaml` | Pas de credentials (variables d'env résolues dans le conteneur) | Partiel |
| 9 | Lire `docker-compose.yml` | Pas de credentials en clair, seulement le chemin vers .env | Partiel |
| 10 | `docker inspect mcp_postgres` | Hook bloque docker inspect | Résolu |
| 11 | `docker compose config` | Hook bloque docker compose config | Résolu |
| 12 | `/proc/$(pgrep)/environ` | Hook + MCP dans le conteneur | Résolu |
| 13 | `env` / `printenv` | Credentials pas dans le shell host | Résolu |

### Accès via le code et les fichiers

| # | Vecteur | Protection | Statut |
|---|---------|-----------|--------|
| 14 | Script Python `import psycopg2` | Hook + pas de credentials sur le host | Résolu |
| 15 | `uv run python -c "..."` | Pas de credentials + pas de route réseau | Résolu |
| 16 | Lire les volumes de données | Volumes gérés par le docker-compose des bases (séparé) | Résolu |
| 17 | Lire les volumes MongoDB | Volumes gérés par le docker-compose des bases (séparé) | Résolu |
| 18 | Modifier `config.yaml` pour désactiver l'anonymisation | config.yaml monté en `:ro` | Résolu |
| 19 | Modifier le code source | Code dans l'image Docker, pas accessible | Résolu |

### Abus des outils MCP

| # | Vecteur | Protection | Statut |
|---|---------|-----------|--------|
| 20 | Alias SQL (`SELECT email AS x`) | Traçage de lignée SQL (sqlglot) | Résolu |
| 21 | Encodage (`encode(email::bytea, 'hex')`) | `encode`/`convert_from`/`convert_to` bloqués | Résolu |
| 22 | Extraction via messages d'erreur | Scan des erreurs (partiel) | Mitigé |
| 23 | Concaténation (`first_name || email`) | Traçage de lignée SQL | Résolu |

### Vecteurs cloud

| # | Vecteur | Protection | Statut |
|---|---------|-----------|--------|
| 24 | Lire le fichier service account (BigQuery) | Credentials dans le conteneur uniquement | Résolu |
| 25 | `gcloud auth print-access-token` | Hook + pas de config GCP sur le host | Résolu |
| 26 | `~/.snowflake/connections.toml` | Credentials dans le conteneur uniquement | Résolu |
| 27 | Variables d'env AWS | Credentials dans le conteneur uniquement | Résolu |

### Vecteurs avancés

| # | Vecteur | Protection | Statut |
|---|---------|-----------|--------|
| 28 | Modifier `docker-compose.yml` pour exposer un port | Hook + nécessite `docker compose up` | Mitigé |
| 29 | `strace -p <pid>` sur le processus MCP | Hook bloque strace/ltrace | Résolu |
| 30 | `docker logs` | Hook bloque docker logs | Résolu |

**Bilan** : 27/30 vecteurs résolus, 3 mitigés. Cette liste n'est pas exhaustive : d'autres vecteurs non encore identifiés peuvent exister.

---

## Limitations connues

### Sécurité réseau

- **Pas de TLS natif** : le serveur MCP communique en HTTP. Pour du HTTPS,
  placer un reverse proxy devant (voir `REVERSE_PROXY_HELP.md`)
- **Pas de RBAC** : tous les clients authentifiés ont le même niveau d'accès.
  Pas de distinction par utilisateur ni de permissions granulaires par base
- **Pas d'audit par utilisateur** : les logs tracent les requêtes mais pas
  l'identité de l'appelant (un seul Bearer token partagé)

### Détection PII

- **Noms de personnes aliasés** : `SELECT first_name AS f` - la lignée SQL
  remonte à la colonne source, mais si le value scan ne reconnaît pas la valeur
  comme un nom (ex: prénom peu courant), elle passe en clair
- **Champs calculés MongoDB** : un `$group` avec alias produit des champs
  absents du schéma d'origine → pas anonymisés
- **PII dans la requête elle-même** : `WHERE email = 'jean@example.com'` -
  l'email transite dans la requête. Hors scope du pipeline
- **Sur-anonymisation possible** : des colonnes non-PII dont le nom matche
  un pattern (ex: `kanban_columns.name` → détecté PERSON) sont anonymisées
  à tort. Résolvable via overrides dans `config.yaml`

### Architecture

- **Hooks contournables** : les hooks Claude Code interceptent les commandes
  Bash, mais l'agent peut reformuler (`bash -c "ps\ql"`, heredocs, base64...).
  Les hooks sont un filet, pas un mur - l'isolation réseau est la vraie protection
- **Dépendance au socket Docker** : en mode sécurisé, la sécurité repose sur
  le fait que l'agent n'a pas accès au socket Docker. Si le socket est monté
  ou si l'agent a des droits Docker, toute l'isolation tombe
