# Contributing Guidelines

Thanks for your interest in contributing to `packit-service`.

The following is a set of guidelines for contributing to `packit-service`.
Use your best judgement, and feel free to propose changes to this document in a pull request.

By contributing to this project you agree to the Developer Certificate of Origin (DCO). This document is a simple statement that you, as a contributor, have the legal right to submit the contribution. See the [DCO](DCO) file for details.

## Reporting Bugs

Before creating a bug report, please check a [list of known issues](https://github.com/packit-service/packit-service/issues) to see
if the problem has already been reported (or fixed in a master branch).

If you're unable to find an open issue addressing the problem, [open a new one](https://github.com/packit-service/packit-service/issues/new).
Be sure to include a **descriptive title and a clear description**. Ideally, please provide:

- version of packit-service and packit you are using (`pip3 freeze | grep packit`)

If possible, add a **code sample** or an **executable test case** demonstrating the expected behavior that is not occurring.

**Note:** If you find a **Closed** issue that seems like it is the same thing that you're experiencing, open a new issue and include a link to the original issue in the body of your new one.
You can also comment on the closed issue to indicate that upstream should provide a new release with a fix.

### Suggesting Enhancements

Enhancement suggestions are tracked as GitHub issues.
When you are creating an enhancement issue, **use a clear and descriptive title** and **provide a clear description of the suggested enhancement** in as many details as possible.

## Guidelines for Developers

If you would like to contribute code to the `packit-service` project, this section is for you!

### Is this your first contribution?

Please take a few minutes to read GitHub's guide on [How to Contribute to Open Source](https://opensource.guide/how-to-contribute/).
It's a quick read, and it's a great way to introduce yourself to how things work behind the scenes in open-source projects.

### Dependencies

If you are introducing a new dependency, please make sure it's added to:

- [setup.cfg](setup.cfg)

### How to contribute code to packit-service

1. Create a fork of this repository.
2. Create a new branch just for the bug/feature you are working on.
3. Once you have completed your work, create a Pull Request, ensuring that it meets the requirements listed below.

### Requirements for Pull Requests (PR)

- Use `pre-commit` (see [below](#checkerslintersformatters--pre-commit)).
- Use common sense when creating commits, not too big, not too small. You can also squash them at the end of review. See [How to Write a Git Commit Message](https://chris.beams.io/posts/git-commit/).
- Cover new code with a test case (new or existing one).
- All tests have to pass.
- Rebase against updated `master` branch before creating a PR to have linear git history.
- Create a PR against the `master` branch.
- The `mergit` label:
  - Add it to instruct CI and/or reviewer that you're really done with the PR.
  - Anyone else can add it too if they think the PR is ready to be merged.
- Status checks SHOULD all be green.
  - Reviewer(s) have final word and HAVE TO run tests locally if they merge a PR with a red CI.

### Checkers/linters/formatters & pre-commit

To make sure our code is [PEP8](https://www.python.org/dev/peps/pep-0008/) compliant, we use:

- [black code formatter](https://github.com/psf/black)
- [Flake8 code linter](http://flake8.pycqa.org)
- [mypy static type checker](http://mypy-lang.org)

There's a [pre-commit](https://pre-commit.com) config file in [.pre-commit-config.yaml](.pre-commit-config.yaml).
To [utilize pre-commit](https://pre-commit.com/#usage), install pre-commit with `pip3 install pre-commit` and then either:

- `pre-commit install` - to install pre-commit into your [git hooks](https://githooks.com). pre-commit will from now on run all the checkers/linters/formatters on every commit. If you later want to commit without running it, just run `git commit` with `-n/--no-verify`.
- Or if you want to manually run all the checkers/linters/formatters, run `pre-commit run --all-files`.

### Changelog

When you are contributing to changelog, please follow these suggestions:

- The changelog is meant to be read by everyone. Imagine that an average user
  will read it and should understand the changes.
- Every line should be a complete sentence. Either tell what is the change that the tool is doing or describe it precisely:
  - Bad: `Use search method in label regex`
  - Good: `Packit now uses search method when...`
- And finally, with the changelogs we are essentially selling our projects:
  think about a situation that you met someone at a conference and you are
  trying to convince the person to use the project and that the changelog
  should help with that.

### Running packit-service locally

Since packit-service is already a fairly complex system, it's not trivial to
run it locally. This is why we are running everything in containers.

This repository contains composefile for
[docker-compose](https://github.com/docker/compose). Before you run it, we
suggest you to open file and read all the comments.

You can also run only certain pieces of packit-service for local development
(e.g. worker, database or httpd).

When you are running httpd and making requests to it, make sure that `server_name` configuration file in `packit-service.yaml` is set. Then you **need** to make requests to httpd using that hostname (which can be done by creating a new entry in `/etc/hosts` on your laptop). Flask literally checks if the request is meant for it by comparing `Host` from the HTTP request with the value of [`SERVER_NAME`](https://flask.palletsprojects.com/en/1.1.x/config/#SERVER_NAME). The `SERVER_NAME` value also has to include port number if it differs from the default, hence your `packit-service.yaml` should contain something like this:

```yaml
server_name: "dev.packit.dev:8443"
```

and `/etc/hosts` (replace `172.18.0.5` with actual IP address from `packit-service` logs):

```
172.18.0.5  dev.packit.dev
```

With these you should be able to make requests:

```
$ curl -k --head https://dev.packit.dev:8443/api/
HTTP/1.1 200 OK
Date: Fri, 10 Apr 2020 10:12:42 GMT
Server: Apache/2.4.43 (Fedora) OpenSSL/1.1.1d mod_wsgi/4.6.6 Python/3.7
Content-Length: 3851
Content-Type: text/html; charset=utf-8
```

Proof:

```
packit-service           | 172.18.0.1 - - [10/Apr/2020:10:22:35 +0000] "HEAD /api/ HTTP/1.1" 200 -
```

### Generating GitHub webhooks

If you need to create a webhook payload, you can utilize script `files/scripts/webhook.py`. It is able to create a minimal json with the webhook payload and send it to p-s instance of your choice (the default is localhost:8443). Pull request changes are only supported right now. For more info, check out the readme:

```
$ GITHUB_TOKEN=the-token python3 files/scripts/webhook.py --help
Usage: webhook.py [OPTIONS] <NAMESPACE/PROJECT>

Options:
  --hostname TEXT      Hostname of packit-service where we should connect
  --github-token TEXT  GitHub token so we can reach the api
  --pr INTEGER         ID of the pull request
  --help               Show this message and exit.
```

### Database

We are using two databases right now: redis (task scheduler for celery) and postgres (persistent data store).

Take a look at [alembic](https://alembic.sqlalchemy.org/en/latest/cookbook.html#building-uptodate), the project which handles migrations and schema versioning for sqlalchemy.

#### How to check what's inside postgres?

Get shell inside the container (or pod). E.g. with docker-compose:

```
$ docker-compose exec -ti postgres bash
bash-4.2$
```

Invoke psql interactive shell:

```
bash-4.2$ psql
psql (10.6)
Type "help" for help.

postgres=#
```

Connect to packit database:

```
postgres=# \connect packit
You are now connected to database "packit" as user "postgres".
packit=#
```

Get help

```
packit=# \?
```

or

```
packit=# \h
```

List tables

```
packit=# \dt
             List of relations
 Schema |      Name       | Type  | Owner
--------+-----------------+-------+--------
 public | alembic_version | table | packit
 public | git_projects    | table | packit
```

Look inside a table

```
packit=# select * from git_projects;
 id | namespace | repo_name
----+-----------+-----------
(0 rows)
```

# Testing

Tests are stored in [tests/](/tests) directory and tests using [requre](https://github.com/packit-service/requre) are stored in [tests_requre/](/tests_requre).

## Test categories

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
   `tests_requre/openshift_integration/`:

- A checkout of packit-service is built as a container image and deployed to
  openshift as a job while the root process is pytest.
- With these, we are making sure that tools we use run well inside [the non-standard OpenShift environment](.https://developers.redhat.com/blog/2016/10/21/understanding-openshift-security-context-constraints/)
- [requre](https://github.com/packit-service/requre) and/or
  [flexmock](https://flexmock.readthedocs.io/en/latest/) is suppose to be
  used to handle remote interactions and secrets so we don't touch production
  systems while running tests in CI

4. End To End tests (so far we have none of these):

- These tests run against a real deployment of packit-service.
- It's expected to send real inputs inside the service and get actual results
  (observable in GitHub, COPR, Fedora infra etc.)
- [requre](https://github.com/packit-service/requre) is used to record the
  remote interactions which are then replayed in CI.

## Running tests locally

You can run unit and integration tests locally in a container:

```
make test_image && make check_in_container
```

To select a subset of the whole test suite, set `TEST_TARGET`. For example to
run only the unit tests use:

```
TEST_TARGET=tests/unit make check_in_container
```

## Openshift tests using requre

This testsuite uses [requre project](https://github.com/packit-service/requre) project to
to store and replay data for tests.

### General requirements

- Set up docker and allow your user access it:

  ```bash
  sudo dnf -y install docker
  sudo groupadd docker
  sudo usermod -a -G docker $(whoami)
  echo '{ "insecure-registries": ["172.30.0.0/16"] }' | sudo tee  /etc/docker/daemon.json
  sudo systemctl restart docker

  newgrp docker
  ```

- Install and run local openshift cluster:
  ```bash
  sudo dnf install origin-clients python3-openshift
  oc cluster up --base-dir=/tmp/openshift_cluster
  ```

### Data regeneration

- remove files which you want to regenerate:
  ```bash
  rm -r tests_requre/test_data/test_*
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
  PermissionError: [Errno 13] Permission denied: '/src-packit-service/tests_requre/test_data/test_fedpkg'
  ```
  You have to create test data directory `mkdir -p tests_requre/test_data`. This directory is part of git repo, so it should not be deleted.
- If you have troubles with requre data regeneration
  - Stop your openshift cluster first
    ```
    oc cluster down
    ```
  - Remove all docker images (including openshift itself) (there is some issue, that openshift uses sometimes some old images)
    ```
    docker rmi -f $(docker images -q)
    ```

### Check it without secrets

If you want to simulate Zuul environment locally you can do so in the following way:

Delete all secrets from OpenShift cluster. This will ensure that no real secrets are used.

```bash
oc delete secrets --all
```

Re-build the images:

```
make service
make worker
```

Generate and deploy fake secrets (you need to have [deployment repository](https://github.com/packit-service/deployment) cloned):

**Note: We highly recommend to clone deployment repository to a temporary location since the command below will overwrite secrets stored in deployment/secrets/dev**

```bash
ansible-playbook --extra-vars="deployment_dir=<PATH_TO_LOCAL_DEPLOYMENT_DIR>" files/deployment.yaml
```

Verify that everything will work also inside zuul. Use the command:

```bash
make check-inside-openshift-zuul
```

## Running tests in CI

For running E2E tests in CI, an instance of OpenShift cluster is deployed and setup in following way:

```
The server is accessible via web console at:
https://127.0.0.1:8443/console
You are logged in as:
User:     developer
Password: <any value>
```

and two projects are created:

```
* myproject
  packit-dev-sandbox

Using project "myproject".
```

Both images `packit-service` and `packit-service-worker` are built from source of current PR and deployed into the Openshift cluster using:

```
$ DEPLOYMENT=dev make deploy
```

**Note: All secrets for PR testing are fake(randomly generated), so it is not possible to communicate with real services (e.g github or copr) for PR testing.**

As the last step playbook [zuul-tests.yaml](/files/zuul-tests.yaml) is executed.

Thank you for your interest!
packit team.
