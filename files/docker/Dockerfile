# Image for the web service (httpd), for celery worker see files/docker/Dockerfile.worker

FROM quay.io/packit/base:fedora

ARG SOURCE_BRANCH
RUN  if [[ -z $SOURCE_BRANCH ]]; then \
echo -e "\nMissing SOURCE_BRANCH build argument! Please add \
\"--build-arg SOURCE_BRANCH=<val>\" to the build command to specify it!\n\
This is the branch used when installing other Packit projects (e.g. ogr, packit).\n" && exit 1;\
fi

ENV USER=packit \
    HOME=/home/packit

WORKDIR /src

COPY files/ ./files/
RUN ansible-playbook -vv -c local -i localhost, files/install-deps.yaml \
    && dnf clean all

COPY setup.py setup.cfg ./
# setuptools-scm
COPY .git ./.git
COPY packit_service/ ./packit_service/

RUN git rev-parse HEAD >/.packit-service.git.commit.hash \
    && git show --quiet --format=%B HEAD >/.packit-service.git.commit.message \
    && ansible-playbook -vv -c local -i localhost, files/recipe.yaml

# no need to rm /src, it will stay in the image anyway

COPY alembic.ini ./
COPY alembic/ ./alembic/

EXPOSE 8443

CMD ["/usr/bin/run_httpd.sh"]
