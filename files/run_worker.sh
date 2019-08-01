#!/usr/bin/bash

# $APP defines where's the module (or package)
if [[ -z ${APP} ]]; then
    echo "APP not defined or empty, exiting"
    exit 1
fi

printf "packit:x:$(id -u):0:Packit Service:/home/packit:/bin/bash\n" >>/home/packit/passwd

export PACKIT_HOME=/home/packit
mkdir --mode=0700 -p ${PACKIT_HOME}/.ssh
install -m 0400 /packit-ssh/id_rsa ${PACKIT_HOME}/.ssh/
install -m 0400 /packit-ssh/id_rsa.pub ${PACKIT_HOME}/.ssh/
install -m 0400 /packit-ssh/config ${PACKIT_HOME}/.ssh/config

exec celery worker --app=${APP} --loglevel=debug --concurrency=1
