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

Les agents IA ont-ils réellement besoin d'accéder à vos vraies données pour vous aider à déboguer une base ou du code ?

MCP DB Results Anonymizer est un MCP Server Python qui agit comme **proxy anonymisant** entre un agent IA (Claude, GPT, etc.) et des bases de données (PostgreSQL, MySQL, SQL Server + MongoDB).

**L'agent ne voit jamais les données réelles ni les credentials de connexion.**

```
┌──────────────┐                                                        
│              │         ┌─────────────────────────────────────────────┐
│   Agent IA   │         │                                             │     ┌──────────────┐
│  (Claude,    │         │          MCP-DB-RESULTS-ANONYMIZER          │┌───►│  PostgreSQL  │
│   Codex...)  │         │                                             ││    └──────────────┘
│              │         │  1. Reçoit la requête de l'agent            ││    ┌──────────────┐
│  Ne voit     │         │  2. Exécute la requête sur la DB            │├───►│    MySQL     │
│  JAMAIS les  │         │  3. Détecte les colonnes PII (schéma+regex) ││    └──────────────┘
│  vraies      │◄──MCP──►│  4. Pseudonymise (Faker + seed hash)        ││    ┌──────────────┐
│  données     │         │  5. Renvoie les données fakées              │├───►│  SQL Server  │
│              │         │                                             ││    └──────────────┘
│  Ne connait  │         │  Credentials stockés côté serveur           ││    ┌──────────────┐
│  JAMAIS les  │         │  Mapping cohérent en SQLite local           │└───►│   MongoDB    │
│  credentials │         │                                             │     └──────────────┘
│              │         └─────────────────────────────────────────────┘
└──────────────┘                                │
                                                ▼
                                        ┌──────────────┐
                                        │ SQLite local │
                                        │ (mappings    │
                                        │  hashés)     │
                                        └──────────────┘
```

## Pourquoi ?

Quand un agent IA interroge une base de données, **tout le résultat transite par les serveurs du fournisseur IA** - noms, emails, téléphones, adresses, credentials de connexion.

Aucune solution existante ne résout ce problème de bout en bout via le protocole MCP. Les alternatives (mcp-presidio, anonymize.dev) anonymisent *après* que l'agent a déjà vu les données. Ici, l'agent n'a **aucun accès direct** à la DB : il passe obligatoirement par le MCP server qui anonymise avant de lui renvoyer.

Le MCP est le **mur** (sécurité by design). Le projet propose aussi des hooks d'agent de code (Claude Code, Codex, OpenCode, Cursor) en complément, comme **filet de sécurité** - voir [docs/SECURITY.fr.md](docs/SECURITY.fr.md).

### Le flux vu par l'agent

1. L'agent transmet la requête SQL au proxy MCP
2. Le proxy exécute la requête sur la vraie base de données
3. Le proxy détecte les colonnes contenant des données personnelles (PII) - voir [docs/DETECTION.fr.md](docs/DETECTION.fr.md)
4. Il remplace les vraies valeurs par des pseudonymes
5. Il renvoie uniquement le résultat anonymisé à l'agent

**L'agent ne voit jamais les vraies données.** Les vrais noms, vrais salaires, etc. ne quittent jamais le proxy.

**Pourquoi envoyer les résultats à l'agent ?** Parce que son rôle est d'aider à analyser et interpréter les résultats - repérer des doublons, des anomalies, reformuler les données. Pour ça, il a besoin de voir la structure et le contenu du résultat. Sans les données, il ne pourrait que corriger la syntaxe SQL sans jamais vérifier que le résultat correspond à ce que l'utilisateur cherche.

L'anonymiseur est le compromis : l'agent reçoit des données **réalistes en structure** (bonnes colonnes, bon nombre de lignes, types corrects) mais **fausses en contenu** pour tout ce qui est sensible. Ça lui permet d'aider concrètement sans exposer de données personnelles.

## Conçu pour / Pas conçu pour

### Ce pour quoi le MCP EST conçu

| Cas d'usage | Pourquoi ça marche |
|---|---|
| **Débugger une requête qui plante** | L'agent corrige votre syntaxe SQL, vos jointures, vos colonnes manquantes - le résultat anonymisé prouve que le fix fonctionne |
| **Vérifier la qualité des données** | Les valeurs NULL, NaN, colonnes vides, types de données, doublons - tout est préservé tel quel à travers l'anonymisation |
| **Comprendre le schéma et les relations** | `describeTable` renvoie les vrais noms de colonnes, types et clés étrangères |
| **Générer des fixtures de test** | `generateTestFixtures` exporte des données réalistes mais entièrement fictives |
| **Valider la logique d'une requête** | Nombre de lignes, structure du GROUP BY, cardinalité des JOIN - tout est préservé |

### Ce pour quoi le MCP N'EST PAS conçu

| Anti-pattern | Pourquoi ça ne marche pas |
|---|---|
| **Analyser les valeurs après anonymisation** | Noms, emails, adresses sont remplacés par des équivalents fictifs - « De quelle ville viennent la majorité des clients ? » est sans réponse |
| **Analyse statistique sur des résultats anonymisés** | Les salaires sont perturbés de ±15%, les dates sont décalées - moyennes, tendances et distributions ne correspondent pas à la réalité |
| **Comparer des valeurs spécifiques entre requêtes** | La pseudonymisation est déterministe *au sein d'une session*, mais les valeurs restent fictives - n'en tirez pas de conclusions métier |
| **Auditer les vraies données** | Par design, l'agent ne voit jamais les vraies PII - si vous devez vérifier les valeurs réelles, interrogez la base directement |

> **En résumé** : le MCP répond à *« pourquoi cette requête plante ? »* et *« la structure des données est-elle correcte ? »*, pas à *« que disent les données ? »*.

## Architecture

Le serveur MCP est publié sur **Docker Hub** : [`dnjfr/mcp-db-results-anonymizer`](https://hub.docker.com/r/dnjfr/mcp-db-results-anonymizer).

L'installeur interactif crée un répertoire d'infrastructure autonome qui tire l'image depuis Docker Hub - aucun build local nécessaire.

Le serveur MCP se connecte aux bases via un réseau Docker partagé (`mcp-db-results-anonymizer-network`). Les bases de données sont gérées séparément - le MCP ne les embarque pas.

**Prérequis** : Docker + Docker Compose.

## Installation

```bash
git clone https://github.com/dnjfr/mcp-db-results-anonymizer.git
cd mcp-db-results-anonymizer
bash install.sh
```

L'installeur crée un répertoire **au même niveau** que le projet, séparé du code source :

```
projects/
├── mcp-db-results-anonymizer/      ← code source (ce repo)
└── db-results-anonymizer-infra/    ← créé par l'installeur
```

Il vous guide pas à pas :
1. Choix du nom du répertoire (défaut : `db-results-anonymizer-infra`)
2. Configuration des credentials (génération automatique possible)
3. Démarrage de l'infrastructure (PostgreSQL + MongoDB + serveur MCP)
4. Choix des bases de démo à importer
5. Connexion de votre agent de code (Claude Code, Codex, OpenCode ou Cursor) - enregistrement MCP + hooks de sécurité installés automatiquement

<details>
<summary>Bases de démo disponibles</summary>

Toutes les bases de démo sont préfixées `demo_` pour éviter les conflits avec vos propres bases.

| N° | Base | Contenu |
|---|---|---|
| 1 | demo_pagila | Vidéoclub - 600 clients, adresses, paiements (PostgreSQL) |
| 2 | demo_chinook | Musique - 60 clients, emails, factures (PostgreSQL) |
| 3 | demo_employees | RH - 300k employés, salaires, dates de naissance (PostgreSQL) |
| 4 | demo_adventureworks | E-commerce/RH - 20k personnes, emails, téléphones (PostgreSQL) |
| 5 | demo_ecommerce | 10 000 clients fictifs (MongoDB) |

Pour désinstaller toutes les démos : `make uninstall-demos`

</details>

<details>
<summary>Conflits de ports</summary>

L'installeur expose des ports hôte par défaut. Si vous avez déjà des services sur ces ports, éditez le `docker-compose.yml` dans le répertoire d'infrastructure après installation :

| Service | Par défaut | Remplacer par |
|---|---|---|
| PostgreSQL | `5434:5432` | `5435:5432` (ou tout port libre) |
| MongoDB | `27017:27017` | `27018:27017` |
| MySQL | `3306:3306` | `3307:3306` |
| SQL Server | `1433:1433` | `1434:1433` |

Ces ports ne servent qu'à l'**accès depuis l'hôte** (ex. connexion avec un client SQL). Le serveur MCP se connecte aux bases via le réseau Docker interne et n'est pas affecté par un changement de port.

</details>

<details>
<summary>Connecter vos propres bases existantes</summary>

Si vous avez déjà des bases de test, elles doivent rejoindre le réseau Docker `mcp-db-results-anonymizer-network` :

```yaml
# Votre docker-compose.yml
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

Puis configurez la connexion dans `~/.mcp-db-results-anonymizer/config.yaml` (voir [docs/CONFIGURATION.fr.md](docs/CONFIGURATION.fr.md)) :

```yaml
databases:
  ma_base:
    type: postgresql
    host: mcp_postgres          # nom du conteneur
    port: 5432
    user: ${POSTGRES_USER}
    password: ${POSTGRES_PASSWORD}
    database: ma_base
```

</details>

<details>
<summary>Différence entre global et local</summary>

| | `setup-global` | `setup-local` |
|---|---|---|
| Enregistrement | `~/.claude.json` (scope user) | `.mcp.json` (scope projet) |
| Portée | Tous les projets | Ce répertoire uniquement |

</details>

<details>
<summary>Agents de code supportés</summary>

Comme il s'agit d'un serveur MCP standard, n'importe quel agent compatible MCP peut s'y connecter. Le menu **Installation / Agent setup** de l'installeur (option 1) configure chacun - enregistrement du MCP **et** son hook de sécurité, avec un choix de portée globale ou locale :

| Agent | Endpoint | Config (globale / locale) | Hook de sécurité |
|---|---|---|---|
| Claude Code | `/sse` | `~/.claude.json` / `.mcp.json` | `PreToolUse` dans `settings.json` |
| Codex | `/mcp` (streamable-http) | `~/.codex/config.toml` / `.codex/config.toml` | `[[hooks.PreToolUse]]` dans `config.toml` |
| OpenCode | `/sse` | `~/.config/opencode/opencode.json` / `opencode.json` | Plugin JS (`tool.execute.before`) |
| Cursor | `/sse` | `~/.cursor/mcp.json` / `.cursor/mcp.json` | `preToolUse` dans `hooks.json` |

Le serveur expose `/sse` et `/mcp` en parallèle, donc tous les agents se connectent à la même instance. Les quatre partagent les mêmes règles de blocage (`scripts/security-hook.sh`). Voir [HOOKS.fr.md](docs/HOOKS.fr.md).

</details>

## Exemple d'utilisation

Un utilisateur demande à son agent IA de corriger une requête sur `demo_employees`. La requête utilise les noms de colonnes MySQL classiques (`emp_no`, `salary`, `salaries`), mais cette base utilise un **schéma PostgreSQL différent** (`id`, `amount`, `salary`) - l'agent ne peut pas deviner, il doit regarder :

```sql
J'ai fait une requête dans la base demo_employees mais elle plante.
Peux-tu m'expliquer pourquoi et la corriger ?

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

L'agent tente de l'exécuter via le MCP. Ça échoue, alors il investigue :

```
Je vais vérifier le schéma réel de la base demo_employees pour identifier les erreurs dans ta requête.

Je vais vérifier le schéma des trois tables utilisées dans ta requête.

● Called mcp-db-results-anonymizer 3 times

Ta requête contient 4 erreurs liées au schéma réel de la base. Voici le détail :

┌─────┬───────────────────────────┬──────────────────────────────────┬───────────────────────────────────────────────────────────────────────────────┐
│  #  │  Erreur dans ta requête   │         Valeur correcte          │                                  Explication                                  │
├─────┼───────────────────────────┼──────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────┤
│ 1   │ FROM employees e          │ FROM employees.employee e        │ La table s'appelle employees.employee (schéma employees, table employee)      │
├─────┼───────────────────────────┼──────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────┤
│ 2   │ JOIN salaries s           │ JOIN employees.salary s          │ La table s'appelle employees.salary (pas salaries)                            │
├─────┼───────────────────────────┼──────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────┤
│ 3   │ JOIN titles t             │ JOIN employees.title t           │ La table s'appelle employees.title (pas titles)                               │
├─────┼───────────────────────────┼──────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────┤
│ 4   │ e.emp_no / s.emp_no /     │ e.id / s.employee_id /           │ La clé primaire de employee est id, et la clé étrangère dans salary et title  │
│     │ t.emp_no                  │ t.employee_id                    │ est employee_id                                                               │
├─────┼───────────────────────────┼──────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────┤
│ 5   │ s.salary                  │ s.amount                         │ La colonne de salaire s'appelle amount, pas salary                            │
└─────┴───────────────────────────┴──────────────────────────────────┴───────────────────────────────────────────────────────────────────────────────┘

Voici la requête corrigée :
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

Tu veux que je l'exécute pour vérifier qu'elle fonctionne ?

```

Si l'utilisateur accepte 

```
● Called mcp-db-results-anonymizer

La requête fonctionne, voici les 5 résultats :

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

Attention : les noms et les montants sont pseudonymisés par le proxy d'anonymisation de la base. 
Ce ne sont pas les vraies données - les id sont fiables, mais les valeurs PII (noms, salaires) sont 
remplacées par des faux à chaque requête.
```

L'agent renvoie la **requête corrigée** qui fonctionne à l'utilisateur et l'agent n'a jamais vu les vraies PII.

> **A noter** : le proxy MCP n'intercepte que les requêtes exécutées. Un agent IA qui connaît le schéma peut corriger une requête cassée sans jamais l'exécuter, dans ce cas, le MCP n'intervient pas car aucune donnée ne transite. Ce n'est pas un problème de sécurité (pas de données = rien à protéger), mais ça signifie que l'agent ne vérifiera pas que le résultat est correct. Pour qu'il valide le résultat, demandez-lui explicitement : « exécute la requête corrigée pour vérifier ».

## Outils MCP

| Outil | Description |
|---|---|
| `listTables` | Liste les tables (SQL) ou collections (NoSQL) d'une base |
| `describeTable` | Décrit le schéma + détecte automatiquement les colonnes PII |
| `querySql` | Exécute un SELECT et renvoie les résultats anonymisés |
| `queryNosql` | Exécute une requête MongoDB (`find`) anonymisée |
| `queryNosqlAggregate` | Exécute un pipeline d'agrégation MongoDB anonymisé |
| `generateTestFixtures` | Exporte N lignes/documents anonymisés en JSON ou CSV |

## Fonctionnalités

- **4 bases de données couvertes** : PostgreSQL, MySQL, SQL Server, MongoDB (voir [docs/CONFIGURATION.fr.md](docs/CONFIGURATION.fr.md))
- **[Détection PII en 5 couches](docs/DETECTION.fr.md)** : classification manuelle, patterns sur colonnes, regex sur valeurs, lignée SQL via sqlglot, value scan fallback
- **Pseudonymisation déterministe** : Faker avec seed basé sur `hash(valeur + sel_session)`
- **Validation SQL read-only** multi-dialecte, fail-closed
- **Défense en profondeur** : conteneur isolé + hooks + Bearer token + rate limiting
- **Stack légère** : pas de ML, pas de Presidio - pattern matching + Faker + sqlglot

## Structure du projet

```
mcp-db-results-anonymizer/          ← code source (Docker Hub: dnjfr/mcp-db-results-anonymizer)
├── src/
│   ├── server.py                   # Point d'entrée MCP (FastMCP)
│   ├── config.py                   # Chargement config YAML
│   ├── database/                   # Connecteurs SQL + MongoDB
│   ├── detection/                  # Pattern matching, lignée SQL, value scan
│   ├── anonymizer/                 # Pseudonymisation Faker + perturbation numérique
│   ├── storage/                    # SQLite local (mappings hashés)
│   ├── security/                   # Validation SQL, auth Bearer, rate limiting
│   └── tools/                      # Outils MCP (query, metadata, fixtures)
├── scripts/                        # Hooks de sécurité partagés (tous agents)
├── infra/                          # Templates pour l'installeur
├── docs/                           # Documentation détaillée
├── install.sh                      # Installeur interactif
├── config.example.yaml             # Template de configuration
├── Dockerfile                      # Image du serveur MCP
└── Makefile                        # Build/push Docker Hub + enregistrement MCP
```

## Limitations connues

- **Noms de personnes** : si un prénom peu courant échappe au value scan, il passe en clair
- **MongoDB aggregate** : les champs calculés absents du schéma d'origine ne sont pas anonymisés
- **PII dans la requête** : `WHERE email = 'jean@example.com'` - l'email transite dans la requête elle-même
- **Pas de TLS natif** : placer un reverse proxy devant pour du HTTPS (voir [docs/REVERSE_PROXY_HELP.fr.md](docs/REVERSE_PROXY_HELP.fr.md))

## Documentation

| Document | Contenu |
|---|---|
| [docs/CONFIGURATION.fr.md](docs/CONFIGURATION.fr.md) | Modes de fonctionnement, config.yaml, bases supportées, mapping, authentification |
| [docs/DETECTION.fr.md](docs/DETECTION.fr.md) | Types PII détectés, pipeline de détection, classification manuelle |
| [docs/HOOKS.fr.md](docs/HOOKS.fr.md) | Hooks de sécurité (Claude Code, Codex, OpenCode, Cursor), vecteurs bloqués |
| [docs/SECURITY.fr.md](docs/SECURITY.fr.md) | Modèle de menace, 30 vecteurs d'attaque, protections |
| [docs/REVERSE_PROXY_HELP.fr.md](docs/REVERSE_PROXY_HELP.fr.md) | Déploiement réseau nginx + TLS |

## Licence

Apache License 2.0
