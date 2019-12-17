#!/usr/bin/bash

# $APP defines where's the module (or package)
if [[ -z ${APP} ]]; then
    echo "APP not defined or empty, exiting"
    exit 1
fi

if [[ ${DEPLOYMENT} == "prod" ]]; then
  LOGLEVEL="info"
else
  LOGLEVEL="debug"
fi

source /src-packit-service/files/setup_env_in_openshift.sh

mkdir --mode=0700 -p "${PACKIT_HOME}/.ssh"
pushd "${PACKIT_HOME}/.ssh"
install -m 0400 /packit-ssh/id_rsa .
install -m 0400 /packit-ssh/id_rsa.pub .
install -m 0400 /packit-ssh/config .
grep -q pkgs.fedoraproject.org known_hosts || ssh-keyscan pkgs.fedoraproject.org >>known_hosts
popd

exec celery worker --app="${APP}" --loglevel=${LOGLEVEL} --concurrency=1
