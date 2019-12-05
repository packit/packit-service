#!/usr/bin/bash

set -x

source /src-packit-service/files/setup_env_in_openshift.sh

id

cat $HOME/.config/packit-service.yaml

# start redis server for tests
redis-server --port 6379 & sleep 5

pytest-3 -vvv tests_requre/openshift_integration/
