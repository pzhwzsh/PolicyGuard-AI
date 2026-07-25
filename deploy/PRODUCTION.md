# Production deployment

1. Copy `.env.production.example` to `.env.production` and replace every `CHANGE_ME`.
2. Create `deploy/secrets/postgres_password.txt` with the same PostgreSQL password.
3. Configure either OIDC (`OIDC_ISSUER_URL`, `OIDC_AUDIENCE`, roles and tenant claims) or the
   legacy tenant/admin keys. OIDC validates issuer signatures through JWKS and enforces
   `user`, `reviewer`, and `admin` role hierarchy.
4. Set `METRICS_API_KEY` and create matching `deploy/secrets/metrics_api_key.txt` plus a
   `grafana_admin_password.txt`. Never commit either file.
5. Put certificates at `deploy/nginx/certs/fullchain.pem` and `privkey.pem` before enabling
   the `edge` profile. The API remains loopback-only without that profile.
6. Run `docker compose -f docker-compose.prod.yml --profile observability up -d --build`.
   Add `--profile edge` only after certificate and DNS validation.
7. Verify `/health`, authenticated `/metrics`, Grafana, API/worker JSON logs, and OTLP traces
   when `OTEL_EXPORTER_OTLP_ENDPOINT` is configured. Then run a backup and dry-run restore.

The migration is a one-shot dependency of both long-running services. API and worker share
the upload volume and model cache. PostgreSQL is not exposed on the host. The example binds
the API only to loopback so a TLS reverse proxy remains the public boundary.
