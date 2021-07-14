#!/usr/bin/bash

# $APP defines where's the module (or package)
if [[ -z ${APP} ]]; then
    echo "APP not defined or empty, exiting"
    exit 1
fi

# CELERY_COMMAND says whether to run Celery worker or beat (task scheduler)
DEFAULT_CELERY_COMMAND="worker"
CELERY_COMMAND="${CELERY_COMMAND:-$DEFAULT_CELERY_COMMAND}"

if [[ ${DEPLOYMENT} == "prod" ]]; then
  LOGLEVEL="INFO"
else
  LOGLEVEL="DEBUG"
fi

source /usr/bin/setup_env_in_openshift.sh

mkdir --mode=0700 -p "${PACKIT_HOME}/.ssh"
pushd "${PACKIT_HOME}/.ssh"
install -m 0400 /packit-ssh/id_rsa .
install -m 0400 /packit-ssh/id_rsa.pub .
install -m 0400 /packit-ssh/config .
grep -q pkgs.fedoraproject.org known_hosts || ssh-keyscan pkgs.fedoraproject.org >>known_hosts
popd

# define queues to serve
DEFAULT_QUEUES="short-running,long-running"
QUEUES="${QUEUES:-$DEFAULT_QUEUES}"

if [[ "${CELERY_COMMAND}" == "beat" ]]; then
    # when using the database backend, celery beat must be running for the results to be expired.
    # https://docs.celeryproject.org/en/stable/userguide/periodic-tasks.html#starting-the-scheduler
    exec celery --app="${APP}" beat --loglevel=${LOGLEVEL} --pidfile=/tmp/celerybeat.pid --schedule=/tmp/celerybeat-schedule

elif [[ "${CELERY_COMMAND}" == "worker" ]]; then
    # concurrency: Number of concurrent worker processes/threads/green threads executing tasks.
    # prefetch-multiplier: How many messages to prefetch at a time multiplied by the number of concurrent processes.
    # http://docs.celeryproject.org/en/latest/userguide/optimizing.html#prefetch-limits
    exec celery --app="${APP}" worker --loglevel=${LOGLEVEL} --concurrency=1 --prefetch-multiplier=1 --queues="${QUEUES}"
fi
