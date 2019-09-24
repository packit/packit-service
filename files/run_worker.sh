#!/usr/bin/bash

# $APP defines where's the module (or package)
if [[ -z ${APP} ]]; then
    echo "APP not defined or empty, exiting"
    exit 1
fi

export PACKIT_HOME=/home/packit

cp ${PACKIT_HOME}/passwd.packit ${PACKIT_HOME}/passwd

# The passwd/nss_wrapper magic is needed for fedpkg
export LD_PRELOAD=libnss_wrapper.so
export NSS_WRAPPER_PASSWD=${HOME}/passwd
export NSS_WRAPPER_GROUP=/etc/group

printf "packit:x:$(id -u):0:Packit Service:/home/packit:/bin/bash\n" >> ${PACKIT_HOME}/passwd

mkdir --mode=0700 -p ${PACKIT_HOME}/.ssh
install -m 0400 /packit-ssh/id_rsa ${PACKIT_HOME}/.ssh/
install -m 0400 /packit-ssh/id_rsa.pub ${PACKIT_HOME}/.ssh/
install -m 0400 /packit-ssh/config ${PACKIT_HOME}/.ssh/config

exec celery-3 worker --app=${APP} --loglevel=info --concurrency=1
