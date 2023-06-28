# Contributing Guidelines

Please follow common guidelines for our projects [here](https://github.com/packit/contributing).

## Reporting Bugs

- [List of known issues](https://github.com/packit/packit-service/issues); if you need to create a new issue, you can do so [here](https://github.com/packit/packit-service/issues/new).
- Getting a version of `packit`:<br>
  `rpm -q packit` or `pip3 freeze | grep packitos`

## Building images locally

For building images the SOURCE_BRANCH build arg is required. It allows selecting which branch should be
used during the automated build and test process, based on the deployment stage it was executed from.

In the Dockerfiles SOURCE_BRANCH is set to:

- SOURCE_BRANCH env variable, which is available in custom build process([docs](https://docs.docker
  .com/docker-hub/builds/advanced/))
- zuul.branch ansible variable, which is provided in zuul-ci environment. ([docs](- [scripts](scripts/) - devops scripts used in multiple repositories
  ))

When you are invoking 'make' manually, you must provide:

- variable SOURCE_BRANCH for make targets:

e.g.
`SOURCE_BRANCH=main`

- arg `--build-arg SOURCE_BRANCH=value` when using podman build

e.g.

    podman build --build-arg SOURCE_BRANCH=stable
    docker-compose build --build-arg SOURCE_BRANCH=stable

If SOURCE_BRANCH is empty build will fail.
If SOURCE_BRANCH is not empty and is not `main` or `stable` then `main` value will be used.

## Running packit-service locally

Since packit-service is already a fairly complex system, it's not trivial to
run it locally. This is why we run everything in containers.

This repository contains [docker-compose.yml](./docker-compose.yml) for
[docker-compose](https://github.com/docker/compose)
(can be also [used with podman](https://fedoramagazine.org/use-docker-compose-with-podman-to-orchestrate-containers-on-fedora)).
Before you run it, we suggest that you open the file and read all the comments.
You can also run only certain pieces of packit-service for local development
(e.g. worker, database or service/httpd).
You also need to populate `secrets/packit/dev/` manually, for instructions
see [deployment repo](https://github.com/packit/deployment/tree/main/secrets).

When you are running service/httpd and making requests to it,
make sure that `server_name` configuration file in `packit-service.yaml` is set.

### tokman

To make tokman work with docker-compose create a `config.py` file in `./secrets/packit/dev/tokman-files` using this [template](https://github.com/packit/tokman/blob/main/config.py.example).
In `.secrets/packit/dev/packit-service.yaml` fix url to tokman from `http://tokman` to `http://tokman:8000`

### binding on localhost

```yaml
server_name: service.localhost:443
```

and you should be able to make requests:

    $ curl -k --head https://service.localhost:443/api/
    HTTP/1.1 200 OK
    Date: Wed, 23 Feb 2022 14:01:41 GMT
    Server: Apache/2.4.51 (Fedora) OpenSSL/1.1.1l mod_wsgi/4.7.1 Python/3.9
    Content-Length: 3824
    Content-Type: text/html; charset=utf-8

Port 443 is used because the certificate is bundled with it
otherwise `dashboard.localhost` can not reach `service.localhost/api`.

For this reason `docker-compose` needs access to ports lower than 1024:

    echo "net.ipv4.ip_unprivileged_port_start=443" > /etc/sysctl.d/docker-compose.conf; sysctl --system

### binding on other hosts

If you are not binding the service on `localhost`
then you **need** to make requests to httpd using the hostname
(which can be done by creating a new entry in `/etc/hosts` on your laptop)
and you have to provide a route to that host.

Flask literally checks if the request is meant for it by comparing `Host`
from the HTTP request with the value of
[`SERVER_NAME`](https://flask.palletsprojects.com/en/1.1.x/config/#SERVER_NAME).
The `SERVER_NAME` value also has to include port number if it differs from the
default, hence your `packit-service.yaml` should contain something like this:

```yaml
server_name: "dev.packit.dev:8443"
```

and `/etc/hosts` (replace `172.18.0.5` with actual IP address from `packit-service` logs):

    172.18.0.5  dev.packit.dev

With these you should be able to make requests:

    $ curl -k --head https://dev.packit.dev:8443/api/
    HTTP/1.1 200 OK
    Date: Fri, 10 Apr 2020 10:12:42 GMT
    Server: Apache/2.4.43 (Fedora) OpenSSL/1.1.1d mod_wsgi/4.6.6 Python/3.7
    Content-Length: 3851
    Content-Type: text/html; charset=utf-8

Proof:

    packit-service           | 172.18.0.1 - - [10/Apr/2020:10:22:35 +0000] "HEAD /api/ HTTP/1.1" 200 -

## Generating GitHub webhooks

If you need to create a webhook payload, you can utilize script `files/scripts/webhook.py`.
It is able to create a minimal json with the webhook payload and send it to
p-s instance of your choice (the default is `dev.packit.dev:8443`).
Pull request changes are only supported right now. For more info:

    $ python3 files/scripts/webhook.py --help
    Usage: webhook.py [OPTIONS] <NAMESPACE/PROJECT>

    Options:
    --hostname TEXT      Hostname of packit-service where we should connect.
    --github-token TEXT  GitHub token so we can reach the api.
    --pr INTEGER         ID of the pull request.
    --help               Show this message and exit.

## Database

Please refer to details [here](docs/database/).

## Testing

Tests are stored in the [tests/](/tests) directory and tests requiring openshift are stored in [tests_openshift/](/tests_openshift)
(e.g. tests that needs to touch the database or that uses [requre](https://github.com/packit/requre) framework).

### Test categories

We have multiple test categories within packit-service:

1. Unit tests — stored in `tests/unit/` directory:

- These tests don't require external resources.
- They are meant to exercise independent functions (usually in utils) or
  abstractions, such as classes.
- The tests should be able to be run locally easily.

2. Integration tests — stored in `tests/integration/`:

- If a test is executing a command or talking to a service, it's an
  integration test.

3. Integration tests which run within an OpenShift pod — stored in
   `tests_openshift/openshift_integration/`:

- A checkout of packit-service is built as a container image and deployed to
  openshift as a job while the root process is pytest.
- With these, we are making sure that tools we use run well inside [the non-standard OpenShift environment](.https://developers.redhat.com/blog/2016/10/21/understanding-openshift-security-context-constraints/)
- [requre](https://github.com/packit/requre) and/or
  [flexmock](https://flexmock.readthedocs.io/en/latest/) is supposed to be
  used to handle remote interactions and secrets, so we don't touch production
  systems while running tests in CI

4. End-To-End tests (so far we have none of these):

- These tests run against a real deployment of packit-service.
- It's expected to send real inputs inside the service and get actual results
  (observable in GitHub, COPR, Fedora infra etc.)
- [requre](https://github.com/packit/requre) is used to record the
  remote interactions which are then replayed in CI.

### Running tests locally

You can run unit and integration tests locally in a container;
make sure that you have the podman package installed, then:

    make build-test-image
    make check-in-container

To select a subset of the whole test suite, set `TEST_TARGET`. For example to
run only the unit tests use:

    TEST_TARGET=tests/unit make check-in-container

#### **Database tests**

Database tests can be run using a dedicated target.

    make check-db

To run them you need docker-compose. Otherwise, you can run the same
using _Openshift_ and following the instructions below.

### Running "reverse-dep" tests locally

In order to use a locally checked out, development version of Packit in the
test image, build a "reverse-dep" test image:

    make build-revdep-test-image

By default, 'packit' is expected to be found at `../packit`. Set `PACKIT_PATH`
to customize this.

Once the image is built, run the tests in a container as usual:

    make check-in-container

### Openshift tests using requre

This testsuite uses [requre project](https://github.com/packit/requre)
to store and replay data for tests.

#### General requirements

- Set up docker and allow your user access to it:

  ```bash
  sudo dnf -y install docker
  sudo groupadd docker
  sudo usermod -a -G docker $(whoami)
  echo '{ "insecure-registries": ["172.30.0.0/16"] }' | sudo tee  /etc/docker/daemon.json
  sudo systemctl restart docker

  newgrp docker
  ```

- Install and run local openshift cluster with `oc cluster up`:

  ```bash
  sudo dnf install origin-clients python3-openshift
  oc cluster up --base-dir=/tmp/openshift_cluster
  ```

- If you want to use `minishift` instead of `oc cluster up`,
  check [this](https://github.com/packit/deployment#minishift).

#### Data regeneration

- remove files which you want to regenerate:
  ```bash
  rm -r tests_openshift/openshift_integration/test_data/test_*
  ```
- Run the tests with the secrets - the response files will be regenerated (container images for `worker` and `test_image` are done in this step)
  ```bash
  make check-inside-openshift PATH_TO_SECRETS=<absolute-path-to-valid-secrets>
  ```

#### Debugging

- to display all openshift pods:
  ```bash
  oc status
  ```
- get information from pod (e.g. testing progress) use information about pods from previous output
  ```bash
  oc logs pod/packit-tests-pdg6p
  ```

#### Troubleshooting

- If you got:
  ```
  PermissionError: [Errno 13] Permission denied: '/src/tests_openshift/test_data/test_fedpkg'
  ```
  You have to create test data directory `mkdir -p tests_openshift/test_data`. This directory is part of git repo, so it should not be deleted.
- If you have troubles with requre data regeneration
  - Stop your openshift cluster first
    ```
    oc cluster down
    ```
  - Remove all docker images (including openshift itself) (there is an issue, that openshift sometimes uses some old images)
    ```
    docker rmi -f $(docker images -q)
    ```

#### Check it without secrets

If you want to simulate Zuul environment locally you can do so in the following way:

Delete all secrets from OpenShift cluster. This will ensure that no real secrets are used.

    oc delete secrets --all

Re-build the images:

    make service
    make worker

Generate and deploy fake secrets (you need to have [deployment repository](https://github.com/packit/deployment) cloned):

**Note: We highly recommend to clone deployment repository to a temporary location since the command below will overwrite secrets stored in deployment/secrets/packit/dev**

    ansible-playbook --extra-vars="deployment_dir=<PATH_TO_LOCAL_DEPLOYMENT_DIR>" files/deployment.yaml

Verify that everything will work also inside zuul. Use the command:

    make check-inside-openshift-zuul

### Running tests in CI

For running E2E tests in CI, an instance of OpenShift cluster is deployed and setup in following way:

    The server is accessible via web console at:
    https://127.0.0.1:8443/console
    You are logged in as:
    User:     developer
    Password: <any value>

and two projects are created:

    * myproject
    packit-dev-sandbox

    Using project "myproject".

Both images `packit-service` and `packit-worker` are built from source of current PR and deployed into the Openshift cluster using:

    DEPLOYMENT=dev make deploy

**Note: All secrets for PR testing are fake(randomly generated), so it is not possible to communicate with real services (e.g github or copr) for PR testing.**

As the last step playbook [zuul-tests.yaml](/files/zuul-tests.yaml) is executed.

### Updating the test image

The image in which tests are running
(`quay.io/packit/packit-service-tests:stg`) is rebuilt every time a new commit
shows up in the `main` branch. It is during this build when new dependencies
can be installed or new versions of existing dependencies are pulled in.

This is why adding a new test dependency (or modifying the Ansible playbooks
configuring the test image in some other way) should be done in a PR
_preceding_ the one in which this dependency is used the first time.

In order to have `packit`, `ogr` or `specfile` updated to a version
corresponding to the latest commit of the `main` branch in the respective
project, you should first wait for Copr to [build the RPM] for this commit,
and then retrigger [the last test image build from the main branch] so that
the new RPM is installed.

---

Thank you for your interest!
Packit team.

[packit/packit]: https://github.com/packit/packit
[build the rpm]: https://copr.fedorainfracloud.org/coprs/packit/packit-dev/builds/
[the last test image build from the main branch]: https://github.com/packit/packit-service/actions/workflows/rebuild-and-push-images.yml?query=branch%3Amain
