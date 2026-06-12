#!/usr/bin/env bash
set -e

RUN_MANAGE_PY='poetry run python -m src.manage'

mkdir -p /opt/project/logs

if [ "$1" = "daphne" ] || [ "$1" = "web" ] || [ $# -eq 0 ]; then
    echo 'Collecting static files...'
    $RUN_MANAGE_PY collectstatic --no-input

    echo 'Running migrations...'
    $RUN_MANAGE_PY migrate --no-input

    # App is fully synchronous (no async views / Channels), so it runs on WSGI
    # via gunicorn. Multiple gthread workers give real request parallelism;
    # daphne serialized sync views in a single thread and saturated under load.
    GUNICORN_WORKERS="${GUNICORN_WORKERS:-2}"
    GUNICORN_THREADS="${GUNICORN_THREADS:-4}"
    GUNICORN_TIMEOUT="${GUNICORN_TIMEOUT:-60}"

    exec poetry run gunicorn src.config.wsgi:application \
        --bind 0.0.0.0:8000 \
        --worker-class gthread \
        --workers "$GUNICORN_WORKERS" \
        --threads "$GUNICORN_THREADS" \
        --timeout "$GUNICORN_TIMEOUT" \
        --access-logfile - \
        --error-logfile -
else
    module="$1"
    shift
    exec poetry run python -m "$module" "$@"
fi
