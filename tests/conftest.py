"""Shared pytest fixtures."""
import os

# Ensure required settings exist before app.core.config imports Settings(),
# so unit tests can run without a real .env file.
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/test")
os.environ.setdefault("SYNC_DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/test")
