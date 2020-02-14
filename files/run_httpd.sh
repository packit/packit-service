#!/usr/bin/bash

set -xe

# Generate passwd file based on current uid
grep -v ^packit /etc/passwd > ${HOME}/passwd
printf "packit:x:$(id -u):0:Packit Service:/home/packit:/bin/bash\n" >> ${HOME}/passwd
export LD_PRELOAD=libnss_wrapper.so
export NSS_WRAPPER_PASSWD=${HOME}/passwd
export NSS_WRAPPER_GROUP=/etc/group

pushd /src
# if all containers started at the same time, pg is definitely not ready to serve
# so let's try this for a few times
n=0
until [ $n -ge 7 ]
do
  alembic upgrade head && break
  n=$[$n+1]
  sleep 2
done
popd  # pushd /src
httpd -DFOREGROUND
