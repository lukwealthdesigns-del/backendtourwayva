# Scripts

- `create_super_admin.py` — promotes an EXISTING, verified user to Super Admin (the only way to
  bootstrap the first one; works in production; audited):

      python -m scripts.create_super_admin --email you@example.com

- `sync_rbac.py` — brings the RBAC permission catalog / system roles in the database in step
  with the code (also run automatically at API start-up):

      python -m scripts.sync_rbac

- `seed_dev_data.py` — IMPLEMENTED (Phase 8). Creates a SUPER_ADMIN
  account (a real, loginable one), Free/Premium plans, and a few sample reference places if
  they don't already exist. Run from the project root:

      python -m scripts.seed_dev_data

  Configure `SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD` in `.env` (both
  have safe local-dev defaults). Never run this against a production
  database — it's meant for local/dev/staging only.

Still not implemented: `run_migrations.sh` (use `alembic upgrade head` directly, or the
Docker image's entrypoint, which already runs it automatically).
