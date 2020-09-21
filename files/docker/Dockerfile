# Image for the web service (httpd), for celery worker see files/docker/Dockerfile.worker

FROM fedora:32

ARG SOURCE_BRANCH
RUN  if [[ -z $SOURCE_BRANCH ]]; then \
echo -e "\nMissing SOURCE_BRANCH build argument! Please add \
\"--build-arg SOURCE_BRANCH=<val>\" to the build command to specify it!\n\
This is the branch used when installing other Packit projects (e.g. ogr, packit).\n" && exit 1;\
fi

ENV LANG=en_US.UTF-8 \
    ANSIBLE_PYTHON_INTERPRETER=/usr/bin/python3 \
    ANSIBLE_STDOUT_CALLBACK=debug \
    USER=packit \
    HOME=/home/packit

COPY files/ /src/files/

RUN cd /src/ \
    && dnf install -y ansible \
    && ansible-playbook -vv -c local -i localhost, files/install-deps.yaml \
    && dnf clean all

COPY setup.py setup.cfg files/recipe.yaml files/tasks/httpd.yaml files/tasks/common.yaml files/packit.wsgi files/run_httpd.sh files/setup_env_in_openshift.sh files/packit-httpd.conf /src/
# setuptools-scm
COPY .git /src/.git
COPY packit_service/ /src/packit_service/

RUN cd /src/ \
    && git rev-parse HEAD >/.packit-service.git.commit.hash \
    && git show --quiet --format=%B HEAD >/.packit-service.git.commit.message \
    && ansible-playbook -vv -c local -i localhost, recipe.yaml

# no need to rm /src, it will stay in the image anyway

COPY alembic.ini /src/
COPY alembic/ /src/alembic/

EXPOSE 8443

CMD ["/usr/bin/run_httpd.sh"]
