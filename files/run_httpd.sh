#!/usr/bin/bash

set -xe

# Generate passwd file based on current uid
grep -v ^packit /etc/passwd > ${HOME}/passwd
printf "packit:x:$(id -u):0:Packit Service:/home/packit:/bin/bash\n" >> ${HOME}/passwd
export LD_PRELOAD=libnss_wrapper.so
export NSS_WRAPPER_PASSWD=${HOME}/passwd
export NSS_WRAPPER_GROUP=/etc/group

# uncomment this after we have some migrations
# pushd /src
# alembic upgrade head
# popd

httpd -DFOREGROUND
