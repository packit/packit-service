#!/usr/bin/bash

set -eux

# $APP defines where's the module (or package)
if [[ -z ${APP} ]]; then
    echo "APP not defined or empty, exiting"
    exit 1
fi

# https://www.shellcheck.net/wiki/SC1091
# shellcheck source=/dev/null
source /usr/bin/setup_env_in_openshift.sh

mkdir -p "${PACKIT_HOME}/.ssh"
chmod 0700 "${PACKIT_HOME}/.ssh"
pushd "${PACKIT_HOME}/.ssh"
install -m 0400 /packit-ssh/id_ed25519* .
if [[ -f /packit-ssh/config ]]; then install -m 0400 /packit-ssh/config .; fi
grep -q pkgs.fedoraproject.org known_hosts 2>/dev/null || curl -fsSL https://admin.fedoraproject.org/ssh_known_hosts >> known_hosts
grep -q gitlab.com known_hosts || ssh-keyscan gitlab.com >> known_hosts
popd

# Whether to run Celery worker or beat (task scheduler)
if [[ "${CELERY_COMMAND:=worker}" == "beat" ]]; then
    # when using the database backend, celery beat must be running for the results to be expired.
    # https://docs.celeryq.dev/en/stable/userguide/periodic-tasks.html#starting-the-scheduler
    exec celery --app="${APP}" beat --loglevel="${LOGLEVEL:-DEBUG}" --pidfile=/tmp/celerybeat.pid --schedule=/tmp/celerybeat-schedule

elif [[ "${CELERY_COMMAND}" == "worker" ]]; then
    # define queues to serve
    : "${QUEUES:=short-running,long-running}"
    export QUEUES

    # Number of concurrent worker threads executing tasks.
    : "${CONCURRENCY:=1}"
    export CONCURRENCY

    # Options: solo | prefork |  gevent
    # https://www.distributedpython.com/2018/10/26/celery-execution-pool/
    if ((CONCURRENCY > 1)); then
      : "${POOL:=gevent}"
    else
      : "${POOL:=prefork}"
    fi
    export POOL

    # if this worker serves the long-running queue, it needs the repository cache
    if [[ "$QUEUES" == *"long-running"* ]]; then
      # Can't be set during deployment
      SANDCASTLE_REPOSITORY_CACHE_VOLUME="sandcastle-repository-cache-$(uname --nodename)"
      export SANDCASTLE_REPOSITORY_CACHE_VOLUME
    fi

    # https://docs.celeryq.dev/en/stable/userguide/optimizing.html#optimizing-prefetch-limit
    exec celery --app="${APP}" worker --loglevel="${LOGLEVEL:-DEBUG}" --concurrency="${CONCURRENCY}" --pool="${POOL}" --prefetch-multiplier=1 --queues="${QUEUES}"
fi
