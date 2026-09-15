#!/usr/bin/env bash
# AutoQA developer helper commands (Phase 0).
set -euo pipefail
cd "$(dirname "$0")/.."

case "${1:-help}" in
  up)       docker compose up -d --build ;;
  down)     docker compose down ;;
  logs)     docker compose logs -f "${2:-}" ;;
  ps)       docker compose ps ;;
  test-be)  docker compose run --rm --no-deps backend pytest -v ;;
  test-fe)  docker compose run --rm --no-deps frontend sh -c "npm install --no-audit --no-fund && npm test" ;;
  build)    docker compose build ;;
  restart)  docker compose restart "$@" ;;
  clean)    docker compose down -v --remove-orphans ;;
  *)        echo "Usage: ./scripts/dev.sh {up|down|logs|ps|test-be|test-fe|build|restart|clean}" ;;
esac
