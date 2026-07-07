# Setting up an nginx reverse proxy with TLS

This guide explains how to secure the MCP server in production by placing
an nginx reverse proxy in front of it, with automatic TLS via Let's Encrypt.

## Why a reverse proxy?

The MCP server listens over plain HTTP without encryption. In production, a reverse proxy
provides:

- **TLS/HTTPS** - traffic encryption between client and server
- **SSL termination** - the certificate is managed by nginx, not Python
- **Rate limiting** - protection against abuse
- **Access logs** - HTTP-level traceability

## Prerequisites

- A domain name pointing to your server (e.g. `mcp.example.com`)
- Docker and Docker Compose installed
- Ports 80 and 443 open on the server

## Architecture

```
MCP Client
    │
    ▼ (HTTPS :443)
┌──────────┐
│  nginx   │  ← TLS termination + auth header forwarding
└──────────┘
    │ (HTTP :8080, Docker internal network)
    ▼
┌──────────┐
│ MCP SSE  │  ← MCP anonymizing server
└──────────┘
    │ (db-internal network)
    ▼
┌──────────┐
│ Postgres │
│ MongoDB  │
└──────────┘
```

## nginx configuration

Create a file `nginx/default.conf`:

```nginx
server {
    listen 80;
    server_name mcp.example.com;

    # HTTP → HTTPS redirect
    location / {
        return 301 https://$host$request_uri;
    }

    # Let's Encrypt challenge
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

    # SSE endpoint - requires long timeouts and no buffering
    location /sse {
        limit_req zone=mcp burst=5 nodelay;

        proxy_pass http://mcp-server:8080;
        proxy_http_version 1.1;

        # SSE headers
        proxy_set_header Connection '';
        proxy_set_header Cache-Control 'no-cache';
        proxy_set_header X-Accel-Buffering 'no';

        # No buffering for streaming
        proxy_buffering off;
        chunked_transfer_encoding off;

        # Long timeout for SSE connections
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;

        # Forward client headers
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

## Production Docker Compose

Here is an example `docker-compose.prod.yml` to use alongside
the existing `docker-compose.yml`:

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
    # Inherits from the main docker-compose.yml
    # but does NOT publish port 8080 externally
    ports: []

volumes:
  certbot-etc:
  certbot-var:
```

## Step-by-step setup

### 1. Create the directories

```bash
mkdir -p nginx
```

### 2. Copy the nginx configuration

Copy the configuration block above into `nginx/default.conf`.
Replace `mcp.example.com` with your domain name.

### 3. Obtain the initial certificate

Temporarily comment out the HTTPS `server` block in `nginx/default.conf`,
then:

```bash
# Start nginx in HTTP-only mode
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d nginx

# Obtain the certificate
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm certbot \
  certonly --webroot --webroot-path=/var/www/certbot \
  -d mcp.example.com --email your@email.com --agree-tos --no-eff-email
```

### 4. Enable HTTPS

Uncomment the HTTPS block in `nginx/default.conf`, then:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

### 5. Configure the API key

Add `MCP_API_KEY` to your `~/.mcp-db-results-anonymizer/.env` file:

```bash
echo "MCP_API_KEY=$(openssl rand -hex 32)" >> ~/.mcp-db-results-anonymizer/.env
```

### 6. Configure the MCP client

Update `.mcp.json` to point to the HTTPS proxy:

```json
{
    "mcpServers": {
        "mcp-db-results-anonymizer": {
            "type": "sse",
            "url": "https://mcp.example.com/sse",
            "headers": {
                "Authorization": "Bearer YOUR_API_KEY_HERE"
            }
        }
    }
}
```

## Automatic certificate renewal

The `certbot` container automatically renews certificates every
12 hours. For nginx to pick up the new certificate, add a
cron job on the host:

```bash
# Reload nginx after renewal (add to crontab)
0 0 * * * docker exec mcp_nginx nginx -s reload
```

## Verification

```bash
# Test the HTTPS connection
curl -v https://mcp.example.com/sse \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Accept: text/event-stream"

# Verify the certificate
openssl s_client -connect mcp.example.com:443 -servername mcp.example.com
```
