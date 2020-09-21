#!/usr/bin/bash

set -xe

source /src/setup_env_in_openshift.sh

pushd /src
# if all containers started at the same time, pg is definitely not ready to serve
# so let's try this for a few times
n=0
until [ $n -ge 7 ]
do
  alembic-3 upgrade head && break
  n=$[$n+1]
  sleep 2
done
popd  # pushd /src

httpd -DFOREGROUND
