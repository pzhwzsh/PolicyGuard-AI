# Production deployment

1. Copy `.env.production.example` to `.env.production` and replace every `CHANGE_ME`.
2. Create `deploy/secrets/postgres_password.txt` with the same PostgreSQL password.
3. Put TLS/authentication in the reverse proxy in front of `127.0.0.1:8000`.
4. Run `docker compose -f docker-compose.prod.yml up -d --build`.
5. Verify `/health`, inspect the API and worker logs, then run a backup and dry-run restore.

The migration is a one-shot dependency of both long-running services. API and worker share
the upload volume and model cache. PostgreSQL is not exposed on the host. The example binds
the API only to loopback so a TLS reverse proxy remains the public boundary.
