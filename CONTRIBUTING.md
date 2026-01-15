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

## Style Guide

### License information

All code, that is modules, scripts and other executable files,
must contain a license header, with wording exactly matching that in `LICENSE_HEADER.txt`.
This includes all `.yaml` files.

License header sample:

```
Copyright Contributors to the Packit project.
SPDX-License-Identifier: MIT
```

### Regular expressions

Any non-trivial regular expressions must be accompanied by documentation,
explaining their purpose and function. In cases of very complex regular expressions,
examples of matching strings should be provided.

### Python code

When not specified otherwise, the code should follow standards laid out in PEP 8.

#### Database

Persistent records, such as results of events must be maintained in a database.
Database tables are defined as SQLalchemy models in `packit_service.models`.

Non-trivial methods of models must include docstrings describing the operation
and accurate type hints, especially for the returned value, as incorrect type specified
can lead to complicated run time issues.

Models may implement their own `__repr__` method.

Example:

```python
def __repr__(self):
    return (
        f"GitProjectModel(name={self.namespace}/{self.repo_name}, "
        f"project_url='{self.project_url}')"
    )
```

When records from database are retrieved, their presence must be verified before their use,
unless such a check is already performed somewhere else.

Columns that are to serve as foreign keys for other tables must have
a descriptive name.

##### Database migration scripts

Changes to database tables defined in `packit_service.models` must be accompanied by Alembic migration,
with a revision script placed in `alembic/versions/` path.

The script can be manually edited to account for edge cases.

Script must have a descriptive name. In cases when the script was manually
edited, or if it contains operations that are not self-explanatory, additional
comments should be provided within.

#### Docstrings

With exception of trivial cases, all code must contain accurate and sufficiently
detailed docstrings, formatted with accordance with the PEP 257 standard and in Google-style.

Special emphasis must be placed on any side effects that the code might have.

##### Examples for Google-style docstrings

Functions, arguments, return values, errors raised:

```python
def check_subpath(
    subpath: Path, path: Path, ensure_trailing_slash: bool = False
) -> str:
    """Check if 'subpath' is a subpath of 'path'

    Args:
        subpath: Subpath to be checked.
        path: Path against which subpath is checked.

    Returns:
        'subpath', resolved, in case it is a subpath of 'path'.

    Raises:
        PackitException, if 'subpath' is not a subpath of 'path'.
    """
```

Generators:

```python
def iter_srcs(synced_files: Sequence[SyncFilesItem]) -> Iterator[str]:
    """Iterate over all the src-s in a list of SyncFilesItem

    Args:
        synced_files: List of SyncFilesItem.

    Yields:
        src-s from every SyncFilesItem, one by one.
    """
```

Classes:

- Document attributes.
- If `__init__` args are the same as attributes, don't write a docstring for
  it.
- `self` doesn't need to be documented as an arg for methods.

```python
class SyncFilesItem:
    """Some files to sync to destination

    Think about this as a wrapper around 'rsync'.

    Attributes:
        src: List of paths to sync.
        dest: Destination to sync to.
        mkpath: Create the destination's path component.
        delete: Delete extra files from dest dirs.
        filters: List of rsync filters used for syncing.
    """

    def __init__(
        self,
        src: Sequence[Union[str, Path]],
        dest: Union[str, Path],
        mkpath: bool = False,
        delete: bool = False,
        filters: Optional[List[str]] = None,
    ):
        # pathlib.Path has no support for trailing slashes, but

```

#### Logging

All errors and exceptions should be logged, with sufficient information for
administrators to begin triage.

Default logger configuration should be used, unless there is a substantial reason
to make an exception.

Logger must be initialized from `logging` library with:

```python
logging.getLogger(__name__)
```

#### Events

Events are implemented as objects in `packit_service.events` namespace. Different types of events have their own modules, with a name reflecting their origin. For example `packit_service.events.testing_farm`.

Modules of events may consist of a single file, in simpler cases, or in more complex cases, split into different submodules. All events must inherit from the `Event` class of the `packit_service.events` module.

Event classes may implement override of the `get_dict` method from the parent, providing additional fields to the returned structure, while preserving those from the parent method.

Example:

```python
def get_dict(self, default_dict: Optional[dict] = None) -> dict:
    result = super().get_dict()
    result["result"] = result["result"].value
    result["pr_id"] = self.pr_id
    return result
```

All events need to have an associated parser implemented, as a static method of the `Parser` class in the `packit_service.worker.parser` module.

Parsers process dictionaries and must either return parsed object,
or `None`, if the dictionary doesn't satisfy conditions specific to the event.
In cases when parser returns `None`, an event must be logged at a level of warning.

Additional logging statements should be included to provide information about event
being processed by the parser.

Example:

```python
@staticmethod
def parse_openscanhub_task_started_event(
    event,
) -> Optional[openscanhub.task.Started]:
    if "openscanhub.task.started" not in event.get("topic", ""):
        return None

    task_id = event.get("task_id")
    logger.info(f"OpenScanHub task: {task_id} started.")

    event = openscanhub.task.Started(task_id=task_id)
    if not event.build:
        logger.warning(
            "OpenScanHub task.started is missing association with build. "
            "Package config can not be resolved without it. "
            "Skipping the event.",
        )
        return None
    return event
```

Parser methods of the `Parser` class are referenced both in the `MAPPING` attribute and the `parse_event` method.

#### Actions

Actions performed by Packit, such as scheduling or reporting test results, have handlers,
implemented as subclasses of `Handler` in `packit_service.worker.handlers`.
Handlers are grouped based on what type of action they perform, such as `packit_service.worker.handlers.testing_farm`. Each handler must have `run` method defined, returning a `TaskResult` object.

Example:

```python
def run(self) -> TaskResults:
    self.packit_api.sync_from_downstream(
        dist_git_branch=self.dg_branch,
        upstream_branch=self.project_to_sync.branch,
        sync_only_specfile=True,
    )
    return TaskResults(success=True, details={})

```

Handlers that are triggered by events use `reacts_to` decorator. Handlers can use mixins, to provide methods required for their purpose, such as `ConfigFromEventMixin`.

#### Tests

New code must be covered by some form of automated tests and should be written
in a way that is conducive to testing when possible.

Tests that do not require presence of a cluster during execution, that is unit and integration
tests, must be placed in `tests/unit` or `tests/integration` path.

If a test does require presence of a cluster, it must be placed in the `tests_openshift/database`
or `tests_openshift/service` path.

Data used for testing must be placed in a `data` directory, for example `tests/data`.

All test cases must have a descriptive name and all non-trivial test cases must
contain a docstring, explaining their purpose.

---

Thank you for your interest!
Packit team.

[packit/packit]: https://github.com/packit/packit
[build the rpm]: https://copr.fedorainfracloud.org/coprs/packit/packit-dev/builds/
[the last test image build from the main branch]: https://github.com/packit/packit-service/actions/workflows/rebuild-and-push-images.yml?query=branch%3Amain
