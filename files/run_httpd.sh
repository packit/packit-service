#!/usr/bin/bash

set -x

# Generate passwd file based on current uid
grep -v ^packit /etc/passwd > ${HOME}/passwd
printf "packit:x:$(id -u):0:Packit Service:/home/packit:/bin/bash\n" >> ${HOME}/passwd
export LD_PRELOAD=libnss_wrapper.so
export NSS_WRAPPER_PASSWD=${HOME}/passwd
export NSS_WRAPPER_GROUP=/etc/group

httpd -DFOREGROUND
