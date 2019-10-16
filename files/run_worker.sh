#!/usr/bin/bash

# $APP defines where's the module (or package)
if [[ -z ${APP} ]]; then
    echo "APP not defined or empty, exiting"
    exit 1
fi

source /src-packit-service/files/setup_env_in_openshift.sh

mkdir --mode=0700 -p ${PACKIT_HOME}/.ssh
install -m 0400 /packit-ssh/id_rsa ${PACKIT_HOME}/.ssh/
install -m 0400 /packit-ssh/id_rsa.pub ${PACKIT_HOME}/.ssh/
install -m 0400 /packit-ssh/config ${PACKIT_HOME}/.ssh/config

exec celery-3 worker --app=${APP} --loglevel=info --concurrency=1
