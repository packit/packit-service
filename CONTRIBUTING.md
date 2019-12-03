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
 * version of packit-service and packit you are using (`pip3 freeze | grep packit`)

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
 * [setup.cfg](setup.cfg)

### How to contribute code to packit

1. Create a fork of the `packit-service` repository.
2. Create a new branch just for the bug/feature you are working on.
3. Once you have completed your work, create a Pull Request, ensuring that it meets the requirements listed below.

### Requirements for Pull Requests

* Please create Pull Requests against the `master` branch.
* Please make sure that your code complies with [PEP8](https://www.python.org/dev/peps/pep-0008/).
* One line should not contain more than 100 characters.
* Make sure that new code is covered by a test case (new or existing one).
* We don't like [spaghetti code](https://en.wikipedia.org/wiki/Spaghetti_code).
* The tests have to pass.

### Checkers/linters/formatters & pre-commit

To make sure our code is compliant with the above requirements, we use:
* [black code formatter](https://github.com/ambv/black)
* [Flake8 code linter](http://flake8.pycqa.org)
* [mypy static type checker](http://mypy-lang.org)

There's a [pre-commit](https://pre-commit.com) config file in [.pre-commit-config.yaml](.pre-commit-config.yaml).
To [utilize pre-commit](https://pre-commit.com/#usage), install pre-commit with `pip3 install pre-commit` and then either
* `pre-commit install` - to install pre-commit into your [git hooks](https://githooks.com). pre-commit will from now on run all the checkers/linters/formatters on every commit. If you later want to commit without running it, just run `git commit` with `-n/--no-verify`.
* Or if you want to manually run all the checkers/linters/formatters, run `pre-commit run --all-files`.

#### Changelog

When you are contributing to changelog, please follow these suggestions:

* The changelog is meant to be read by everyone. Imagine that an average user
  will read it and should understand the changes.
* Every line should be a complete sentence. Either tell what is the change that the tool is doing or describe it precisely:
  * Bad: `Use search method in label regex`
  * Good: `Packit now uses search method when...`
* And finally, with the changelogs we are essentially selling our projects:
  think about a situation that you met someone at a conference and you are
  trying to convince the person to use the project and that the changelog
  should help with that.

# Testing

Tests are stored in [tests/](/tests) directory and tests using [requre](https://github.com/packit-service/requre) are stored in [tests-requre/](/tests-requre).


## Test categories

We have multiple test categories within packit-service:

1. Unit tests — stored in `tests/unit/` directory:
  * These tests don't require external resources.
  * They are meant to exercise independent functions (usually in utils) or
    abstractions, such as classes.
  * The tests should be able to be run locally easily.

2. Integration tests — stored in `tests/integration/`:
  * If a test is executing a command or talking to a service, it's an
    integration test.

3. Integration tests which run within an OpenShift pod — stored in
   `tests_requre/openshift_integration/`:
  * A checkout of packit-service is built as a container image and deployed to
    openshift as a job while the root process is pytest.
  * With these, we are making sure that tools we use run well inside [the non-standard OpenShift environment](.https://developers.redhat.com/blog/2016/10/21/understanding-openshift-security-context-constraints/)
  * [requre](https://github.com/packit-service/requre) and/or
    [flexmock](https://flexmock.readthedocs.io/en/latest/) is suppose to be
    used to handle remote interactions and secrets so we don't touch production
    systems while running tests in CI

4. End To End tests (so far we have none of these):
  * These tests run against a real deployment of packit-service.
  * It's expected to send real inputs inside the service and get actual results
    (observable in GitHub, COPR, Fedora infra etc.)
  * [requre](https://github.com/packit-service/requre) is used to record the
    remote interactions which are then replayed in CI.


## Running tests locally

You can run unit and integration tests locally in a container:
```
make test_image && make check_in_container
```

## Openshift tests using requre
This testsuite uses [requre project](https://github.com/packit-service/requre) project to
to store and replay data for tests.

### General requirements
 * Set up docker and allow your user access it:
   ```bash
   sudo dnf -y install docker
   sudo groupadd docker
   sudo usermod -a -G docker $(whoami)
   echo '{ "insecure-registries": ["172.30.0.0/16"] }' | sudo tee  /etc/docker/daemon.json
   sudo systemctl restart docker

   newgrp docker
   ```
 * Install and run local openshift cluster:
   ```bash
   sudo dnf install origins-client python3-openshift
   oc cluster up --base-dir=/tmp/openshift_cluster
   ```

### Data regeneration
 * remove files which you want to regenerate:
   ```bash
   rm -r tests_requre/test_data/test_*
   ```
 * Run the tests with the secrets - the response files will be regenerated (container images for `worker` and `test_image` are done in this step)
   ```bash
   make check-inside-openshift PATH_TO_SECRETS=<absolute-path-to-valid-secrets>
   ```
 * Remove timestamps and another data what are changed every time, to avoid unwanted
   changes of generated files.
   ```bash
   make requre-purge-files
   ```

#### Debugging
 * to display all openshift pods:
   ```bash
   oc status
   ```
 * get information from pod (e.g. testing progress) use information  about pods from previous output
   ```bash
   oc logs pod/packit-tests-pdg6p
   ```

#### Troubleshooting
 * If you got:
   ```
   PermissionError: [Errno 13] Permission denied: '/src-packit-service/tests_requre/test_data/test_fedpkg'
   ```
   You have to create test data directory `mkdir -p tests_requre/test_data`. This directory is part of git repo, so it should not be deleted.
 * If you have troubles with requre data regeneration
   * Stop your openshift cluster first
     ```
     oc cluster down
     ```
   * Remove all docker images (including openshift itself) (there is some issue, that openshift uses sometimes some old images)
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

### Additional configuration for development purposes

#### Copr build

For cases you'd like to trigger a copr build in your copr project, you can configure it in packit configuration of your chosen package:
```yaml
jobs:
- job: copr_build
  trigger: pull_request
  metadata:
    targets:
      - some_targets
    # (Optional) Defaults to 'packit'
    owner: some_copr_project_owner
    # (Optional) Defaults to <github_namespace>-<github_repo>
    project: some_project_name
```

### How to add a new job?

Creating a new job is not hard at all but requires a few steps to be done. This section will walk you through this process.

#### Define job type in Packit

The first step is to define new `JobType` and/or `JobTriggerType` in [packit/config.py](https://github.com/packit-service/packit/blob/master/packit/config.py). If you are defining new job which appears also in `.packit.yaml` you have to update `JOB_CONFIG_SCHEMA` in [schema.py](https://github.com/packit-service/packit/blob/master/packit/schema.py) and add the name of job to enum.
Then I recommend to push this change into your packit fork and change installation of `packit` in both [recipe.yaml](/files/recipe.yaml) and [recipe-tests.yaml](/files/recipe-tests.yaml) to this commit (e.g `git+https://github.com/rpitonak/packit.git@9cae9a0381753148e5bb23121bfebbb948f37b01`).

#### Packit service

Once we have jobs defined in `packit` config we are ready to move on to next steps:

1. Define a new event in [events.py](/packit_service/service/events.py). This is required just when you want to react to new events (e.g github webhooks, fedmsg events, payloads from other APIs). In this file there are representations of those JSON objects.
2. Define parse method in [worker/parser.py](/packit_service/worker/parser.py). Create new static method in `Parser` class which can deserialize new defined event in previous step. Don't forget to call it in `parse_event` method. Write a new test in `test_events.py` to verify that it works well.
3. Depends on type of job - create new handler in one of the `*_handlers.py` files. You need to implement the `run` method where is the whole logic of the handler. In this step take inspiration from other handlers.

### Service configuration

The service configuration is an extension of the user configuration from packit. (`Config` class in [packit/config.py](https://github.com/packit-service/packit/blob/master/packit/config.py).)

To add a new service-related property you need to:

1. Add a property to `ServiceConfig.__init__` in [config.py](/packit_service/config.py).
2. Load the property in `ServiceConfig.get_from_dict`.
2. Add it to the validation schema (`_SERVICE_CONFIG_SCHEMA_PROPERTIES`) in [schema.py](/packit_service/schema.py).
    - Add the property to the `_SERVICE_CONFIG_SCHEMA_REQUIRED` if the property is required.

Thank you for your interest!
packit team.
