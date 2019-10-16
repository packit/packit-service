#!/usr/bin/bash

set -x

source /src-packit-service/files/setup_env_in_openshift.sh

id

cat $HOME/.config/packit-service.yaml

pytest-3 -vv tests/openshift_integration/
