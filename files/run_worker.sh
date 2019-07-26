#!/usr/bin/bash

# $APP defines where's the module (or package)
if [[ -z ${APP} ]]; then
    echo "APP not defined or empty, exiting"
    exit 1
fi

printf "packit:x:$(id -u):0:Packit Service:/home/packit:/bin/bash\n" >>/home/packit/passwd

exec celery worker --app=${APP} --loglevel=debug --concurrency=1
