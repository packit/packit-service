#!/usr/bin/bash

set -x

export PACKIT_HOME=/home/packit
# Generate passwd file based on current uid, needed for fedpkg
grep -v ^packit /etc/passwd > ${PACKIT_HOME}/passwd
printf "packit:x:%s:0:Packit Service:/home/packit:/bin/bash\n" "$(id -u)">> ${PACKIT_HOME}/passwd
export LD_PRELOAD=libnss_wrapper.so
export NSS_WRAPPER_PASSWD=${HOME}/passwd
export NSS_WRAPPER_GROUP=/etc/group
