#!/bin/sh
# Entrypoint: apply DB migrations, then hand off to the service command.
# Fresh deployments get a migrated schema automatically; on existing
# databases `alembic upgrade head` is a cheap no-op version check.
set -e
alembic upgrade head
exec "$@"
