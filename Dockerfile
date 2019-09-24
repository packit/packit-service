# Image for the web service (httpd), for celery worker see Dockerfile.worker

FROM docker.io/usercont/httpd:2.4

ENV LANG=en_US.UTF-8 \
    ANSIBLE_STDOUT_CALLBACK=debug \
    USER=packit \
    HOME=/home/packit

# We need to install packages. httpd:2.4 image has user 1001
USER 0

RUN dnf install -y ansible

COPY files/install-deps.yaml /src/files/

RUN cd /src/ \
    && ansible-playbook -vv -c local -i localhost, files/install-deps.yaml \
    && dnf clean all

COPY setup.py setup.cfg files/recipe.yaml files/packit.wsgi /src/
# setuptools-scm
COPY .git /src/.git
COPY packit_service/ /src/packit_service/

RUN cd /src/ \
    && ansible-playbook -vv -c local -i localhost, recipe.yaml

# TODO: add this logic to files/recipe.yaml
RUN /usr/libexec/httpd-prepare && rpm-file-permissions \
    && chmod -R a+rwx /var/lib/httpd \
    && chmod -R a+rwx /var/log/httpd

USER 1001

CMD ["/usr/bin/run-httpd"]
