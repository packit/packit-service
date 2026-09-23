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
SERVER_NAME=$(sed -nr 's/^server_name: ([^:]+)(:([0-9]+))?$/\1/p' "$PACKIT_SERVICE_CONFIG")
HTTP_PORT=$(sed -nr 's/^server_name: ([^:]+)(:([0-9]+))?$/\3/p' "$PACKIT_SERVICE_CONFIG")

HTTPD_ARGS=(
    --access-log
    --log-to-terminal
    --server-name "${SERVER_NAME}"
    --processes 2
    --restart-interval 28800
    --graceful-timeout 15
    --locale "C.UTF-8"
    --http2
)

if [[ -f /secrets/fullchain.pem && -f /secrets/privkey.pem ]]; then
    HTTPD_ARGS+=(
        --https-port "${HTTP_PORT:-8443}"
        --ssl-certificate-file /secrets/fullchain.pem
        --ssl-certificate-key-file /secrets/privkey.pem
    )
else
    HTTPD_ARGS+=(--port "${HTTP_PORT:-8443}")
fi

# See "mod_wsgi-express-3 start-server --help" for details on
# these options, and the configuration documentation of mod_wsgi:
# https://modwsgi.readthedocs.io/en/master/configuration.html
exec mod_wsgi-express-3 start-server \
    "${HTTPD_ARGS[@]}" \
    /usr/share/packit/packit.wsgi
