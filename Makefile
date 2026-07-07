.PHONY: help start-secured start-dev stop down network setup-global setup-local install-hooks install-mcp-global install-mcp-local uninstall uninstall-mcp docker-build docker-push docker-release

CONFIG_PATH    = $(HOME)/.mcp-db-results-anonymizer/config.yaml
DOCKER_IMAGE   = dnjfr/mcp-db-results-anonymizer
VERSION        = $(shell grep '^version' pyproject.toml | head -1 | sed 's/.*"\(.*\)"/\1/')

help: ## Affiche les commandes disponibles
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' Makefile | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# --- Réseau Docker partagé ---

network:
	@docker network create mcp-db-results-anonymizer-network 2>/dev/null || true

# --- Modes de lancement ---

start-secured: network ## Mode sécurisé : MCP containerisé en SSE sur le réseau mcp-db-results-anonymizer-network
	@docker compose up -d --build
	@$(MAKE) --no-print-directory install-mcp-local
	@echo ""
	@if [ "$$(docker network inspect mcp-db-results-anonymizer-network -f '{{len .Containers}}' 2>/dev/null)" = "1" ]; then \
		echo "⚠  Aucune base de données détectée sur mcp-db-results-anonymizer-network."; \
		echo "   Lancez vos conteneurs DB sur ce réseau avant d'utiliser le MCP."; \
		echo ""; \
	fi
	@echo "Mode sécurisé activé."
	@echo "  - MCP SSE : http://localhost:8080/sse"
	@echo "  - Connectez vos bases au réseau mcp-db-results-anonymizer-network (voir README)"

start-dev: ## Mode dev : MCP en stdio local (uv run), sans Docker
	@$(MAKE) --no-print-directory install-mcp-local
	@echo ""
	@echo "Mode dev activé."
	@echo "  - MCP : stdio (uv run python -m src.server)"
	@echo "  - Configurez vos bases dans $(CONFIG_PATH)"

stop: ## Arrête le conteneur MCP sans le supprimer
	@docker compose stop 2>/dev/null || true

down: ## Supprime le conteneur MCP et son réseau
	@docker compose down 2>/dev/null || true

# --- Hooks de sécurité ---

install-hooks: ## Installe les hooks de sécurité dans les settings utilisateur Claude Code
	@bash scripts/install-hooks.sh

# --- Enregistrement MCP ---

install-mcp-global: ## Enregistre le MCP au niveau utilisateur (~/.claude.json) - disponible dans tous les projets
	@claude mcp remove mcp-db-results-anonymizer -s user 2>/dev/null || true
	@API_KEY=$$(grep -s '^MCP_API_KEY=' ~/.mcp-db-results-anonymizer/.env | cut -d= -f2-); \
	if [ -n "$$API_KEY" ]; then \
		claude mcp add mcp-db-results-anonymizer "http://127.0.0.1:8080/sse" -t sse -s user -H "Authorization: Bearer $$API_KEY"; \
	else \
		claude mcp add mcp-db-results-anonymizer "http://127.0.0.1:8080/sse" -t sse -s user; \
	fi
	@echo "MCP enregistré globalement (~/.claude.json)."
	@echo "Disponible dans tous les projets."

install-mcp-local: ## Génère un .mcp.json local - disponible uniquement dans ce projet
	@API_KEY=$$(grep -s '^MCP_API_KEY=' ~/.mcp-db-results-anonymizer/.env | cut -d= -f2-); \
	if docker ps --format '{{.Names}}' | grep -q mcp_anonymizer; then \
		if [ -n "$$API_KEY" ]; then \
			echo "{\"mcpServers\":{\"mcp-db-results-anonymizer\":{\"type\":\"sse\",\"url\":\"http://localhost:8080/sse\",\"headers\":{\"Authorization\":\"Bearer $$API_KEY\"}}}}" | python3 -m json.tool > .mcp.json; \
		else \
			echo '{"mcpServers":{"mcp-db-results-anonymizer":{"type":"sse","url":"http://localhost:8080/sse"}}}' | python3 -m json.tool > .mcp.json; \
		fi; \
		echo ".mcp.json configuré en mode SSE."; \
	else \
		echo '{"mcpServers":{"mcp-db-results-anonymizer":{"type":"stdio","command":"uv","args":["run","python","-m","src.server"]}}}' | python3 -m json.tool > .mcp.json; \
		echo ".mcp.json configuré en mode stdio."; \
	fi
	@echo "MCP enregistré localement (.mcp.json)."
	@echo "Disponible uniquement dans ce répertoire."

# --- Setup complet ---

setup-global: start-secured install-hooks install-mcp-global ## Installation globale : MCP + hooks, disponible dans tous les projets
	@echo ""
	@echo "Setup global terminé."
	@echo "  - Le MCP est disponible dans tous les projets (~/.claude.json)"
	@echo "  - Configurez vos bases dans $(CONFIG_PATH)"
	@echo "  - Redémarrez Claude Code pour activer les hooks de sécurité."

setup-local: start-secured install-hooks install-mcp-local ## Installation locale : MCP + hooks, disponible uniquement dans ce projet
	@echo ""
	@echo "Setup local terminé."
	@echo "  - Le MCP est disponible uniquement dans ce répertoire (.mcp.json)"
	@echo "  - Configurez vos bases dans $(CONFIG_PATH)"
	@echo "  - Redémarrez Claude Code pour activer les hooks de sécurité."

# --- Docker Hub ---

docker-build: ## Build l'image Docker localement
	@docker build -t $(DOCKER_IMAGE):$(VERSION) -t $(DOCKER_IMAGE):latest .
	@echo "Image construite : $(DOCKER_IMAGE):$(VERSION)"

docker-push: ## Push l'image sur Docker Hub (nécessite docker login)
	@docker push $(DOCKER_IMAGE):$(VERSION)
	@docker push $(DOCKER_IMAGE):latest
	@echo "Image publiée : $(DOCKER_IMAGE):$(VERSION) + latest"

docker-release: docker-build docker-push ## Build + push en une commande

# --- Désinstallation ---

uninstall-mcp: ## Supprime l'enregistrement MCP (global + local) sans toucher aux données
	@claude mcp remove mcp-db-results-anonymizer -s user 2>/dev/null && \
		echo "MCP retiré de ~/.claude.json." || true
	@rm -f .mcp.json && echo ".mcp.json supprimé." || true
	@echo "Enregistrement MCP supprimé. Redémarrez Claude Code."

uninstall: uninstall-mcp ## Désinstallation complète : MCP + conteneur + hooks
	@docker compose down -v 2>/dev/null || true
	@echo "Conteneur MCP supprimé."
	@bash scripts/uninstall-hooks.sh 2>/dev/null && \
		echo "Hooks de sécurité retirés." || true
	@echo ""
	@echo "Désinstallation terminée."
	@echo "  - Le répertoire ~/.mcp-db-results-anonymizer/ (credentials + config) n'a PAS été supprimé."
	@echo "  - Pour le supprimer manuellement : rm -rf ~/.mcp-db-results-anonymizer/"
