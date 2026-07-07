.PHONY: help start stop stop-mcp stop-infra down restart restart-infra restart-all demo-pagila demo-chinook demo-employees demo-adventureworks demo-mongo uninstall-demos db-add install-mcp-global install-mcp-local install-hooks setup-global setup-local uninstall-mcp uninstall

include $(HOME)/.mcp-db-results-anonymizer/.env
export

CONFIG_PATH     = $(HOME)/.mcp-db-results-anonymizer/config.yaml
PG_CONTAINER    = mcp_postgres
MCP_CONTAINER   = mcp_anonymizer
MONGO_CONTAINER = mcp_mongo
PG_USER         = $(POSTGRES_USER)
PAGILA_DIR      = data/pagila
PAGILA_REPO     = https://raw.githubusercontent.com/devrimgunduz/pagila/master
CHINOOK_DIR     = data/chinook
CHINOOK_URL     = https://github.com/lerocha/chinook-database/raw/master/ChinookDatabase/DataSources/Chinook_PostgreSql.sql
EMPLOYEES_DIR   = data/employees
EMPLOYEES_REPO  = https://github.com/h8/employees-database.git
AW_DIR          = data/adventureworks
AW_REPO         = https://github.com/lorint/AdventureWorks-for-Postgres.git
AW_CSV_URL      = https://github.com/Microsoft/sql-server-samples/releases/download/adventureworks/AdventureWorks-oltp-install-script.zip

DEMO_PG_DATABASES  = demo_pagila chinook demo_employees demo_adventureworks
DEMO_MONGO_DATABASE = demo_ecommerce

help: ## Affiche les commandes disponibles
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' Makefile | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ──────────────────────────────────────────────
# Infrastructure (DB + MCP server)
# ──────────────────────────────────────────────

start: ## Lance tout : bases de données + serveur MCP
	@docker network create mcp-db-results-anonymizer-network 2>/dev/null || true
	@docker compose up -d
	@echo "En attente de PostgreSQL..."
	@until docker exec $(PG_CONTAINER) pg_isready -U $(PG_USER) -q 2>/dev/null; do sleep 1; done
	@echo "En attente de MongoDB..."
	@until docker exec $(MONGO_CONTAINER) mongosh --quiet --eval "db.runCommand('ping').ok" 2>/dev/null | grep -q 1; do sleep 1; done
	@echo ""
	@echo "Infrastructure prête."
	@echo "  - PostgreSQL : localhost:5434"
	@echo "  - MongoDB    : localhost:27017"
	@echo "  - MCP SSE    : http://localhost:8080/sse"

stop: ## Arrête tous les conteneurs sans les supprimer (données préservées)
	@docker compose stop 2>/dev/null || true
	@echo "Tous les conteneurs ont été arrêtés."

stop-mcp: ## Arrête uniquement le serveur MCP (l'infra reste active)
	@docker stop $(MCP_CONTAINER) 2>/dev/null || true
	@echo "Serveur MCP arrêté."

stop-infra: ## Arrête l'infra (bases de données) sans toucher au serveur MCP
	@docker stop $(PG_CONTAINER) $(MONGO_CONTAINER) 2>/dev/null || true
	@docker stop mcp_mysql mcp_mssql 2>/dev/null || true
	@echo "Infrastructure arrêtée."

down: ## Supprime tous les conteneurs et réseaux (volumes et données préservés)
	@docker compose down 2>/dev/null || true

restart: ## Redémarre le serveur MCP (pull la dernière image si disponible)
	@echo "Vérification des mises à jour..."
	@docker compose pull mcp-server 2>/dev/null || true
	@docker compose up -d mcp-server
	@echo "Serveur MCP redémarré."

restart-infra: ## Redémarre l'infra (bases de données) sans toucher au serveur MCP
	@docker restart $(PG_CONTAINER) $(MONGO_CONTAINER) 2>/dev/null || true
	@docker restart mcp_mysql mcp_mssql 2>/dev/null || true
	@echo "Infrastructure redémarrée."

restart-all: ## Redémarre tout (serveur MCP + infra, pull les dernières images)
	@echo "Vérification des mises à jour..."
	@docker compose pull 2>/dev/null || true
	@docker compose up -d
	@echo "Tous les conteneurs ont été redémarrés."

# ──────────────────────────────────────────────
# Import de bases utilisateur
# ──────────────────────────────────────────────

db-add: ## Importe votre base PostgreSQL : make db-add name=mabase dump=backup.sql(.gz)
	@if [ -z "$(name)" ] || [ -z "$(dump)" ]; then \
		echo "Usage : make db-add name=mabase dump=chemin/vers/backup.sql(.gz)"; \
		echo ""; \
		echo "Exemple :"; \
		echo "  make db-add name=clients dump=db-backup/clients.sql.gz"; \
		exit 1; \
	fi
	@if [ ! -f "$(dump)" ]; then \
		echo "Erreur : fichier '$(dump)' introuvable."; \
		exit 1; \
	fi
	@echo "Création de la base $(name)..."
	@docker exec $(PG_CONTAINER) psql -U $(PG_USER) -c "DROP DATABASE IF EXISTS $(name);" 2>/dev/null
	@docker exec $(PG_CONTAINER) psql -U $(PG_USER) -c "CREATE DATABASE $(name);"
	@echo "Import du dump..."
	@if echo "$(dump)" | grep -q '\.gz$$'; then \
		zcat "$(dump)" | docker exec -i $(PG_CONTAINER) psql -U $(PG_USER) -d $(name) -q 2>&1 | grep -v "^ERROR:.*role" || true; \
	else \
		docker exec -i $(PG_CONTAINER) psql -U $(PG_USER) -d $(name) -q < "$(dump)" 2>&1 | grep -v "^ERROR:.*role" || true; \
	fi
	@if grep -q "^  $(name):" $(CONFIG_PATH) 2>/dev/null; then \
		echo "Base $(name) déjà présente dans $(CONFIG_PATH)."; \
	else \
		sed -i '/^databases:/a\\n  $(name):\n    type: postgresql\n    host: $${PG_HOST:-mcp_postgres}\n    port: $${PG_PORT:-5432}\n    user: $${POSTGRES_USER}\n    password: $${POSTGRES_PASSWORD}\n    database: $(name)' $(CONFIG_PATH); \
		echo "Base $(name) ajoutée à $(CONFIG_PATH)."; \
	fi
	@docker restart $(MCP_CONTAINER) 2>/dev/null || true
	@echo ""
	@echo "Base $(name) importée. Le serveur MCP a été redémarré."
	@echo "  → Reconnectez le MCP dans Claude Code avec /mcp"

# ──────────────────────────────────────────────
# Bases de démo PostgreSQL (préfixées demo_)
# ──────────────────────────────────────────────

demo-pagila: ## Importe demo_pagila (vidéoclub — 600 clients, adresses, paiements)
	@mkdir -p $(PAGILA_DIR)
	@if [ ! -f $(PAGILA_DIR)/pagila-schema.sql ]; then \
		echo "Téléchargement des fichiers Pagila..."; \
		curl -fSL $(PAGILA_REPO)/pagila-schema.sql -o $(PAGILA_DIR)/pagila-schema.sql; \
		curl -fSL $(PAGILA_REPO)/pagila-insert-data.sql -o $(PAGILA_DIR)/pagila-insert-data.sql; \
	fi
	@echo "Création de la base demo_pagila..."
	@docker exec $(PG_CONTAINER) psql -U $(PG_USER) -c "DROP DATABASE IF EXISTS demo_pagila;" 2>/dev/null
	@docker exec $(PG_CONTAINER) psql -U $(PG_USER) -c "CREATE DATABASE demo_pagila;"
	@echo "Import du schéma..."
	@docker exec -i $(PG_CONTAINER) psql -U $(PG_USER) -d demo_pagila -q < $(PAGILA_DIR)/pagila-schema.sql
	@echo "Import des données..."
	@docker exec -i $(PG_CONTAINER) psql -U $(PG_USER) -d demo_pagila -q < $(PAGILA_DIR)/pagila-insert-data.sql
	@rm -rf $(PAGILA_DIR)
	@if grep -q "^  demo_pagila:" $(CONFIG_PATH) 2>/dev/null; then \
		echo "demo_pagila déjà dans $(CONFIG_PATH)."; \
	else \
		sed -i '/^databases:/a\\n  demo_pagila:\n    type: postgresql\n    host: $${PG_HOST:-mcp_postgres}\n    port: $${PG_PORT:-5432}\n    user: $${POSTGRES_USER}\n    password: $${POSTGRES_PASSWORD}\n    database: demo_pagila' $(CONFIG_PATH); \
		echo "demo_pagila ajoutée à $(CONFIG_PATH)."; \
	fi
	@docker restart $(MCP_CONTAINER) 2>/dev/null || true
	@echo "Base demo_pagila importée. Serveur MCP redémarré."

demo-chinook: ## Importe chinook (musique — 60 clients, emails, factures)
	@mkdir -p $(CHINOOK_DIR)
	@if [ ! -f $(CHINOOK_DIR)/Chinook_PostgreSql.sql ]; then \
		echo "Téléchargement de Chinook..."; \
		curl -fSL $(CHINOOK_URL) -o $(CHINOOK_DIR)/Chinook_PostgreSql.sql; \
	fi
	@echo "Création de la base chinook..."
	@docker exec $(PG_CONTAINER) psql -U $(PG_USER) -c "DROP DATABASE IF EXISTS chinook;" 2>/dev/null
	@docker exec $(PG_CONTAINER) psql -U $(PG_USER) -c "CREATE DATABASE chinook;"
	@echo "Import des données..."
	@sed '/^DROP DATABASE/d;/^CREATE DATABASE/d;/^\\connect/d' $(CHINOOK_DIR)/Chinook_PostgreSql.sql | docker exec -i $(PG_CONTAINER) psql -U $(PG_USER) -d chinook -q
	@rm -rf $(CHINOOK_DIR)
	@if grep -q "^  chinook:" $(CONFIG_PATH) 2>/dev/null; then \
		echo "chinook déjà dans $(CONFIG_PATH)."; \
	else \
		sed -i '/^databases:/a\\n  chinook:\n    type: postgresql\n    host: $${PG_HOST:-mcp_postgres}\n    port: $${PG_PORT:-5432}\n    user: $${POSTGRES_USER}\n    password: $${POSTGRES_PASSWORD}\n    database: chinook' $(CONFIG_PATH); \
		echo "chinook ajoutée à $(CONFIG_PATH)."; \
	fi
	@docker restart $(MCP_CONTAINER) 2>/dev/null || true
	@echo "Base chinook importée. Serveur MCP redémarré."

demo-employees: ## Importe demo_employees (RH — 300k employés, salaires, DOB)
	@if [ ! -d $(EMPLOYEES_DIR) ]; then \
		echo "Téléchargement de Employees..."; \
		git clone --depth 1 $(EMPLOYEES_REPO) $(EMPLOYEES_DIR); \
	fi
	@if [ ! -f $(EMPLOYEES_DIR)/employees_data.sql ]; then \
		echo "Décompression des données..."; \
		bunzip2 -k $(EMPLOYEES_DIR)/employees_data.sql.bz2; \
	fi
	@echo "Création de la base demo_employees..."
	@docker exec $(PG_CONTAINER) psql -U $(PG_USER) -c "DROP DATABASE IF EXISTS demo_employees;" 2>/dev/null
	@docker exec $(PG_CONTAINER) psql -U $(PG_USER) -c "CREATE DATABASE demo_employees;"
	@echo "Import du schéma..."
	@docker exec -i $(PG_CONTAINER) psql -U $(PG_USER) -d demo_employees -q < $(EMPLOYEES_DIR)/employees_schema.sql
	@echo "Import des données (300k lignes, peut prendre quelques minutes)..."
	@docker exec -i $(PG_CONTAINER) psql -U $(PG_USER) -d demo_employees -q < $(EMPLOYEES_DIR)/employees_data.sql
	@rm -rf $(EMPLOYEES_DIR)
	@if grep -q "^  demo_employees:" $(CONFIG_PATH) 2>/dev/null; then \
		echo "demo_employees déjà dans $(CONFIG_PATH)."; \
	else \
		sed -i '/^databases:/a\\n  demo_employees:\n    type: postgresql\n    host: $${PG_HOST:-mcp_postgres}\n    port: $${PG_PORT:-5432}\n    user: $${POSTGRES_USER}\n    password: $${POSTGRES_PASSWORD}\n    database: demo_employees' $(CONFIG_PATH); \
		echo "demo_employees ajoutée à $(CONFIG_PATH)."; \
	fi
	@docker restart $(MCP_CONTAINER) 2>/dev/null || true
	@echo "Base demo_employees importée. Serveur MCP redémarré."

demo-adventureworks: ## Importe demo_adventureworks (e-commerce/RH — 20k personnes, emails, phones)
	@if [ ! -d $(AW_DIR) ]; then \
		echo "Téléchargement d'AdventureWorks..."; \
		git clone --depth 1 $(AW_REPO) $(AW_DIR); \
	fi
	@if [ ! -f $(AW_DIR)/Address.csv ]; then \
		echo "Téléchargement des CSV Microsoft..."; \
		curl -fSL $(AW_CSV_URL) -o $(AW_DIR)/aw.zip; \
		unzip -o $(AW_DIR)/aw.zip "*.csv" -d $(AW_DIR); \
		rm $(AW_DIR)/aw.zip; \
		echo "Conversion des CSV pour PostgreSQL..."; \
		python3 scripts/update_csvs.py $(AW_DIR); \
	fi
	@echo "Création de la base demo_adventureworks..."
	@docker exec $(PG_CONTAINER) psql -U $(PG_USER) -c "DROP DATABASE IF EXISTS demo_adventureworks;" 2>/dev/null
	@docker exec $(PG_CONTAINER) psql -U $(PG_USER) -c "CREATE DATABASE demo_adventureworks;"
	@echo "Copie des fichiers dans le conteneur..."
	@docker cp $(AW_DIR)/ $(PG_CONTAINER):/tmp/adventureworks/
	@echo "Import des données..."
	@docker exec $(PG_CONTAINER) bash -c 'cd /tmp/adventureworks && psql -U $(PG_USER) -d demo_adventureworks -q < install.sql' 2>&1 | tail -5
	@rm -rf $(AW_DIR)
	@docker exec $(PG_CONTAINER) rm -rf /tmp/adventureworks
	@if grep -q "^  demo_adventureworks:" $(CONFIG_PATH) 2>/dev/null; then \
		echo "demo_adventureworks déjà dans $(CONFIG_PATH)."; \
	else \
		sed -i '/^databases:/a\\n  demo_adventureworks:\n    type: postgresql\n    host: $${PG_HOST:-mcp_postgres}\n    port: $${PG_PORT:-5432}\n    user: $${POSTGRES_USER}\n    password: $${POSTGRES_PASSWORD}\n    database: demo_adventureworks' $(CONFIG_PATH); \
		echo "demo_adventureworks ajoutée à $(CONFIG_PATH)."; \
	fi
	@docker restart $(MCP_CONTAINER) 2>/dev/null || true
	@echo "Base demo_adventureworks importée. Serveur MCP redémarré."

# ──────────────────────────────────────────────
# Base de démo MongoDB (préfixée demo_)
# ──────────────────────────────────────────────

demo-mongo: ## Génère 10 000 clients fictifs dans demo_ecommerce (MongoDB)
	@echo "Génération des données fictives via Docker..."
	@docker run --rm \
		--network mcp-db-results-anonymizer-network \
		-e MONGO_HOST=mcp_mongo \
		-e MONGO_PORT=27017 \
		-e MONGODB_USER=$(MONGODB_USER) \
		-e MONGODB_PASSWORD=$(MONGODB_PASSWORD) \
		-v $(CURDIR)/fake_data_mongo.py:/app/fake_data_mongo.py:ro \
		python:3.12-slim \
		bash -c "pip install --quiet faker pymongo && python /app/fake_data_mongo.py"
	@if grep -q "^  demo_ecommerce:" $(CONFIG_PATH) 2>/dev/null; then \
		echo "demo_ecommerce déjà dans $(CONFIG_PATH)."; \
	else \
		sed -i '/^databases:/a\\n  demo_ecommerce:\n    type: mongodb\n    host: $${MONGO_HOST:-mcp_mongo}\n    port: $${MONGO_PORT:-27017}\n    user: $${MONGODB_USER}\n    password: $${MONGODB_PASSWORD}\n    database: demo_ecommerce\n    auth_database: admin' $(CONFIG_PATH); \
		echo "demo_ecommerce ajoutée à $(CONFIG_PATH)."; \
	fi
	@docker restart $(MCP_CONTAINER) 2>/dev/null || true
	@echo "Données MongoDB créées dans demo_ecommerce. Serveur MCP redémarré."

# ──────────────────────────────────────────────
# Désinstallation des démos
# ──────────────────────────────────────────────

uninstall-demos: ## Supprime toutes les bases de démo (demo_*) sans toucher aux bases utilisateur
	@echo "Suppression des bases de démo PostgreSQL..."
	@for db in $(DEMO_PG_DATABASES); do \
		docker exec $(PG_CONTAINER) psql -U $(PG_USER) -c "DROP DATABASE IF EXISTS $$db;" 2>/dev/null && \
			echo "  $$db supprimée." || true; \
		sed -i "/^  $$db:/,/^  [^ ]/{ /^  $$db:/d; /^    /d; }" $(CONFIG_PATH) 2>/dev/null || true; \
	done
	@echo "Suppression de la base de démo MongoDB..."
	@docker exec $(MONGO_CONTAINER) mongosh --quiet \
		-u "$(MONGODB_USER)" -p "$(MONGODB_PASSWORD)" --authenticationDatabase admin \
		--eval "db.getSiblingDB('$(DEMO_MONGO_DATABASE)').dropDatabase()" 2>/dev/null && \
		echo "  $(DEMO_MONGO_DATABASE) supprimée." || true
	@sed -i "/^  $(DEMO_MONGO_DATABASE):/,/^  [^ ]/{ /^  $(DEMO_MONGO_DATABASE):/d; /^    /d; }" $(CONFIG_PATH) 2>/dev/null || true
	@sed -i '/^$$/N;/^\n$$/d' $(CONFIG_PATH) 2>/dev/null || true
	@docker restart $(MCP_CONTAINER) 2>/dev/null || true
	@echo ""
	@echo "Toutes les bases de démo ont été supprimées."
	@echo "Vos bases utilisateur n'ont pas été touchées."

# ──────────────────────────────────────────────
# Enregistrement MCP dans Claude Code
# ──────────────────────────────────────────────

install-mcp-global: ## Enregistre le MCP au niveau utilisateur (~/.claude.json) — disponible dans tous les projets
	@claude mcp remove mcp-db-results-anonymizer -s user 2>/dev/null || true
	@API_KEY=$$(grep -s '^MCP_API_KEY=' ~/.mcp-db-results-anonymizer/.env | cut -d= -f2-); \
	if [ -n "$$API_KEY" ]; then \
		claude mcp add mcp-db-results-anonymizer "http://127.0.0.1:8080/sse" -t sse -s user -H "Authorization: Bearer $$API_KEY"; \
	else \
		claude mcp add mcp-db-results-anonymizer "http://127.0.0.1:8080/sse" -t sse -s user; \
	fi
	@echo "MCP enregistré globalement (~/.claude.json)."

install-mcp-local: ## Génère un .mcp.json local — disponible uniquement dans ce répertoire
	@API_KEY=$$(grep -s '^MCP_API_KEY=' ~/.mcp-db-results-anonymizer/.env | cut -d= -f2-); \
	if [ -n "$$API_KEY" ]; then \
		echo "{\"mcpServers\":{\"mcp-db-results-anonymizer\":{\"type\":\"sse\",\"url\":\"http://localhost:8080/sse\",\"headers\":{\"Authorization\":\"Bearer $$API_KEY\"}}}}" | python3 -m json.tool > .mcp.json; \
	else \
		echo '{"mcpServers":{"mcp-db-results-anonymizer":{"type":"sse","url":"http://localhost:8080/sse"}}}' | python3 -m json.tool > .mcp.json; \
	fi
	@echo "MCP enregistré localement (.mcp.json)."

install-hooks: ## Installe les hooks de sécurité dans les settings utilisateur Claude Code
	@bash scripts/install-hooks.sh

# ──────────────────────────────────────────────
# Setup complet (one-shot)
# ──────────────────────────────────────────────

setup-global: start install-hooks install-mcp-global ## Setup complet : infra + MCP global (~/.claude.json) + hooks
	@echo ""
	@echo "Setup global terminé."
	@echo "  - Le MCP est disponible dans tous les projets"
	@echo "  - Redémarrez Claude Code pour activer les hooks de sécurité"

setup-local: start install-hooks install-mcp-local ## Setup complet : infra + MCP local (.mcp.json) + hooks
	@echo ""
	@echo "Setup local terminé."
	@echo "  - Le MCP est disponible uniquement dans ce répertoire"
	@echo "  - Redémarrez Claude Code pour activer les hooks de sécurité"

# ──────────────────────────────────────────────
# Désinstallation
# ──────────────────────────────────────────────

uninstall-mcp: ## Supprime l'enregistrement MCP (global + local) sans toucher aux données
	@claude mcp remove mcp-db-results-anonymizer -s user 2>/dev/null && \
		echo "MCP retiré de ~/.claude.json." || true
	@rm -f .mcp.json && echo ".mcp.json supprimé." || true
	@echo "Enregistrement MCP supprimé. Redémarrez Claude Code."

uninstall: uninstall-mcp ## Désinstallation complète : MCP + conteneurs + hooks
	@docker compose down -v 2>/dev/null || true
	@echo "Conteneurs supprimés."
	@bash scripts/uninstall-hooks.sh 2>/dev/null && \
		echo "Hooks de sécurité retirés." || true
	@echo ""
	@echo "Désinstallation terminée."
	@echo "  - Le répertoire ~/.mcp-db-results-anonymizer/ (credentials + config) n'a PAS été supprimé."
