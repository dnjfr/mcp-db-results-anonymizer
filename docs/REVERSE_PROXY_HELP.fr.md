# Mise en place d'un reverse proxy nginx avec TLS

Ce guide explique comment sécuriser le serveur MCP en production en plaçant
un reverse proxy nginx devant, avec TLS automatique via Let's Encrypt.

## Pourquoi un reverse proxy ?

Le serveur MCP écoute en HTTP sans chiffrement. En production, un reverse proxy
apporte :

- **TLS/HTTPS** - chiffrement du trafic entre le client et le serveur
- **Terminaison SSL** - le certificat est géré par nginx, pas par Python
- **Rate limiting** - protection contre les abus
- **Logs d'accès** - traçabilité au niveau HTTP

## Prérequis

- Un nom de domaine pointant vers votre serveur (ex: `mcp.example.com`)
- Docker et Docker Compose installés
- Les ports 80 et 443 ouverts sur le serveur

## Architecture

```
Client MCP
    │
    ▼ (HTTPS :443)
┌──────────┐
│  nginx   │  ← TLS termination + auth header forwarding
└──────────┘
    │ (HTTP :8080, réseau interne Docker)
    ▼
┌──────────┐
│ MCP SSE  │  ← serveur MCP anonymiseur
└──────────┘
    │ (réseau db-internal)
    ▼
┌──────────┐
│ Postgres │
│ MongoDB  │
└──────────┘
```

## Configuration nginx

Créer un fichier `nginx/default.conf` :

```nginx
server {
    listen 80;
    server_name mcp.example.com;

    # Redirection HTTP → HTTPS
    location / {
        return 301 https://$host$request_uri;
    }

    # Challenge Let's Encrypt
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
}

server {
    listen 443 ssl;
    server_name mcp.example.com;

    ssl_certificate     /etc/letsencrypt/live/mcp.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mcp.example.com/privkey.pem;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    # Rate limiting
    limit_req_zone $binary_remote_addr zone=mcp:10m rate=30r/m;

    # SSE endpoint - nécessite des timeouts longs et pas de buffering
    location /sse {
        limit_req zone=mcp burst=5 nodelay;

        proxy_pass http://mcp-server:8080;
        proxy_http_version 1.1;

        # Headers SSE
        proxy_set_header Connection '';
        proxy_set_header Cache-Control 'no-cache';
        proxy_set_header X-Accel-Buffering 'no';

        # Pas de buffering pour le streaming
        proxy_buffering off;
        chunked_transfer_encoding off;

        # Timeout long pour les connexions SSE
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;

        # Forward des headers client
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Authorization $http_authorization;
    }

    # Messages endpoint
    location /messages/ {
        limit_req zone=mcp burst=10 nodelay;

        proxy_pass http://mcp-server:8080;
        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Authorization $http_authorization;
    }
}
```

## Docker Compose de production

Voici un exemple de `docker-compose.prod.yml` à utiliser en complément
du `docker-compose.yml` existant :

```yaml
services:
  nginx:
    image: nginx:alpine
    container_name: mcp_nginx
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/default.conf:/etc/nginx/conf.d/default.conf:ro
      - certbot-etc:/etc/letsencrypt:ro
      - certbot-var:/var/www/certbot:ro
    networks:
      - mcp-external
    depends_on:
      - mcp-server
    restart: unless-stopped

  certbot:
    image: certbot/certbot
    container_name: mcp_certbot
    volumes:
      - certbot-etc:/etc/letsencrypt
      - certbot-var:/var/www/certbot
    entrypoint: "/bin/sh -c 'trap exit TERM; while :; do sleep 12h & wait $${!}; certbot renew; done'"

  mcp-server:
    # Reprend la définition du docker-compose.yml principal
    # mais ne publie PAS le port 8080 vers l'extérieur
    ports: []

volumes:
  certbot-etc:
  certbot-var:
```

## Mise en place étape par étape

### 1. Créer les répertoires

```bash
mkdir -p nginx
```

### 2. Copier la configuration nginx

Copier le bloc de configuration ci-dessus dans `nginx/default.conf`.
Remplacer `mcp.example.com` par votre nom de domaine.

### 3. Obtenir le certificat initial

Commenter temporairement le bloc `server` HTTPS dans `nginx/default.conf`,
puis :

```bash
# Démarrer nginx en HTTP uniquement
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d nginx

# Obtenir le certificat
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm certbot \
  certonly --webroot --webroot-path=/var/www/certbot \
  -d mcp.example.com --email votre@email.com --agree-tos --no-eff-email
```

### 4. Activer HTTPS

Décommenter le bloc HTTPS dans `nginx/default.conf`, puis :

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

### 5. Configurer l'API key

Ajouter `MCP_API_KEY` dans votre fichier `~/.mcp-db-results-anonymizer/.env` :

```bash
echo "MCP_API_KEY=$(openssl rand -hex 32)" >> ~/.mcp-db-results-anonymizer/.env
```

### 6. Configurer le client MCP

Mettre à jour `.mcp.json` pour pointer vers le proxy HTTPS :

```json
{
    "mcpServers": {
        "mcp-db-results-anonymizer": {
            "type": "sse",
            "url": "https://mcp.example.com/sse",
            "headers": {
                "Authorization": "Bearer VOTRE_API_KEY_ICI"
            }
        }
    }
}
```

## Renouvellement automatique des certificats

Le conteneur `certbot` renouvelle automatiquement les certificats toutes les
12 heures. Pour que nginx prenne en compte le nouveau certificat, ajouter un
cron sur l'hôte :

```bash
# Recharger nginx après renouvellement (à ajouter dans crontab)
0 0 * * * docker exec mcp_nginx nginx -s reload
```

## Vérification

```bash
# Tester la connexion HTTPS
curl -v https://mcp.example.com/sse \
  -H "Authorization: Bearer VOTRE_API_KEY" \
  -H "Accept: text/event-stream"

# Vérifier le certificat
openssl s_client -connect mcp.example.com:443 -servername mcp.example.com
```
