# Image for the web service (httpd), for celery worker see Dockerfile.worker

FROM fedora:31

ENV LANG=en_US.UTF-8 \
    ANSIBLE_PYTHON_INTERPRETER=/usr/bin/python3 \
    ANSIBLE_STDOUT_CALLBACK=debug \
    USER=packit \
    HOME=/home/packit

RUN dnf install -y ansible

COPY files/install-deps.yaml /src/files/

RUN cd /src/ \
    && ansible-playbook -vv -c local -i localhost, files/install-deps.yaml \
    && dnf clean all

COPY setup.py setup.cfg files/recipe.yaml files/tasks/httpd.yaml files/tasks/common.yaml files/packit.wsgi files/run_httpd.sh /src/
# setuptools-scm
COPY .git /src/.git
COPY packit_service/ /src/packit_service/

RUN cd /src/ \
    && ansible-playbook -vv -c local -i localhost, recipe.yaml \
    && rm -rf /src/

COPY alembic.ini /src/
COPY alembic/ /src/alembic/

EXPOSE 8443

CMD ["/usr/bin/run_httpd.sh"]
