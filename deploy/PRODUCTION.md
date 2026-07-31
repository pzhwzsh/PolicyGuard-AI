# Production deployment

This runbook assumes a Linux server with Docker Compose, a DNS record pointing to the server,
and inbound TCP ports 80 and 443 available. Never commit `.env.production`, SMTP authorization
codes, database passwords, API keys, or TLS private keys.

## Required inputs

- Server IP, Linux distribution, SSH user, and key-based authentication method
- Public domain name and DNS access
- QQ Mail address with SMTP enabled and a generated authorization code
- Strong PostgreSQL, admin, reviewer, metrics, and Grafana passwords
- Model provider endpoints and keys, if external model features are enabled

## Prepare configuration

1. Copy `.env.production.example` to `.env.production` on the server.
2. Replace every `CHANGE_ME` value. Generate `AUTH_CODE_PEPPER` with at least 32 random bytes.
3. Set the same PostgreSQL password in `DATABASE_URL` and
   `deploy/secrets/postgres_password.txt`.
4. Place the certificate chain and private key at
   `deploy/nginx/certs/fullchain.pem` and `deploy/nginx/certs/privkey.pem`.
5. Set the real domain in `deploy/nginx/policyguard.conf` instead of `server_name _`.

Production startup intentionally fails when the authentication pepper is left at its development
default or `SESSION_COOKIE_SECURE` is not `true`.

## Deploy

```bash
docker compose -f docker-compose.prod.yml config
docker compose -f docker-compose.prod.yml build --pull
docker compose -f docker-compose.prod.yml run --rm migrate
docker compose -f docker-compose.prod.yml --profile edge up -d
docker compose -f docker-compose.prod.yml ps
```

Do not use `docker compose down -v` in production because it removes persistent data volumes.

## Acceptance checks

```bash
curl -fsS https://YOUR_DOMAIN/health
curl -fsSI https://YOUR_DOMAIN/login
docker compose -f docker-compose.prod.yml logs --tail=100 api worker edge
```

Verify these behaviors in a browser:

- an unauthenticated visit to `/` reaches `/login` before business data is loaded;
- registration sends a QQ Mail code and never exposes whether an address already exists;
- the session cookie is `Secure`, `HttpOnly`, and `SameSite=Lax`;
- a user cannot read another user's product, workflow, report, or task;
- logout invalidates the server-side session;
- evidence links, human approval, remediation, history, and PDF export complete end to end;
- `/admin` and `/metrics` require their configured credentials.

## Backup and rollback

Back up PostgreSQL and `/app/data` before every release. Keep the previous image tag until the
new version passes acceptance checks. Application rollback and database rollback are separate:
restore the previous image first, and only restore a database backup when the migration is not
backward compatible.
