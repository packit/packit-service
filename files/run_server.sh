#!/usr/bin/bash

set -eux

# if all containers started at the same time, pg is definitely not ready to serve
# so let's try this for a few times
ATTEMPTS=7
n=0
while [[ $n -lt $ATTEMPTS ]]; do
  alembic-3 upgrade head && break
  n=$((n+1))
  sleep 2
done

# If the number of attempts was exhausted: the migration failed.
# Exit with an error.
if [[ $n -eq $ATTEMPTS ]]; then
    echo "Migration failed after $ATTEMPTS attempts. Exiting."
    exit 1
fi

export PACKIT_SERVICE_CONFIG="${HOME}/.config/packit-service.yaml"
HTTPS_PORT=$(sed -nr 's/^server_name: ([^:]+)(:([0-9]+))?$/\3/p' "$PACKIT_SERVICE_CONFIG")

DEPLOYMENT=${DEPLOYMENT:-dev}

# Gunicorn is recommended for prod deployment, because it's more robust and scalable
if [[ "${DEPLOYMENT}" == "prod" || "${DEPLOYMENT}" == "stg" ]]; then
    echo "Running Gunicorn with Uvicorn workers"
    exec gunicorn -k uvicorn.workers.UvicornWorker \
        -b 0.0.0.0:"${HTTPS_PORT:-8443}" \
        --access-logfile - \
        --log-level debug \
        --certfile /secrets/fullchain.pem \
        --keyfile /secrets/privkey.pem \
        "packit_service.service.app:app"
else
    echo "Running Uvicorn in development mode"
    exec uvicorn packit_service.service.app:app \
        --host 0.0.0.0 \
        --port "${HTTPS_PORT:-8443}" \
        --log-level debug \
        --ssl-certfile /secrets/fullchain.pem \
        --ssl-keyfile /secrets/privkey.pem \
        --reload
fi
