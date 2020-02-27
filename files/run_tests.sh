#!/usr/bin/bash

set -x

source /src-packit-service/files/setup_env_in_openshift.sh

id

cat $HOME/.config/packit-service.yaml

alembic upgrade head

pytest-3 -vv tests_requre/

# copy everything to Persistent Volume mount point
cp -vr tests_requre/* /tmp/test_data
