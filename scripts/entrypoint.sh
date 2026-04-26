#!/usr/bin/env bash
set -e

RUN_MANAGE_PY='poetry run python -m src.manage'

mkdir -p /opt/project/logs

if [ "$1" = "daphne" ] || [ $# -eq 0 ]; then
    echo 'Collecting static files...'
    $RUN_MANAGE_PY collectstatic --no-input

    echo 'Running migrations...'
    $RUN_MANAGE_PY migrate --no-input

    exec poetry run daphne src.config.asgi:application -p 8000 -b 0.0.0.0
else
    module="$1"
    shift
    exec poetry run python -m "$module" "$@"
fi
