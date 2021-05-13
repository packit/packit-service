BASE_IMAGE ?= quay.io/packit/base
SERVICE_IMAGE ?= quay.io/packit/packit-service:dev
WORKER_IMAGE ?= quay.io/packit/packit-worker:dev
TEST_IMAGE ?= quay.io/packit/packit-service-tests:stg
TEST_TARGET ?= ./tests/unit ./tests/integration/
CONTAINER_ENGINE ?= $(shell command -v podman 2> /dev/null || echo docker)
ANSIBLE_PYTHON ?= /usr/bin/python3
AP ?= ansible-playbook -vv -c local -i localhost, -e ansible_python_interpreter=$(ANSIBLE_PYTHON)
PATH_TO_SECRETS ?= $(CURDIR)/secrets/
COV_REPORT ?= term-missing
COLOR ?= yes
SOURCE_BRANCH ?= $(shell git branch --show-current)

service: files/install-deps.yaml files/recipe.yaml
	$(CONTAINER_ENGINE) pull $(BASE_IMAGE)
	$(CONTAINER_ENGINE) build --rm -t $(SERVICE_IMAGE) -f files/docker/Dockerfile --build-arg SOURCE_BRANCH=$(SOURCE_BRANCH) .

worker: files/install-deps-worker.yaml files/recipe-worker.yaml
	$(CONTAINER_ENGINE) pull $(BASE_IMAGE)
	$(CONTAINER_ENGINE) build --rm -t $(WORKER_IMAGE) -f files/docker/Dockerfile.worker --build-arg SOURCE_BRANCH=$(SOURCE_BRANCH) .

check:
	find . -name "*.pyc" -exec rm {} \;
	PYTHONPATH=$(CURDIR) PYTHONDONTWRITEBYTECODE=1 python3 -m pytest --color=$(COLOR) --verbose --showlocals --cov=packit_service --cov-report=$(COV_REPORT) $(TEST_TARGET)

build-test-image: files/install-deps-worker.yaml files/install-deps.yaml files/recipe-tests.yaml
	$(CONTAINER_ENGINE) build --rm -t $(TEST_IMAGE) -f files/docker/Dockerfile.tests --build-arg SOURCE_BRANCH=$(SOURCE_BRANCH) .

check-in-container:
	@# don't use -ti here in CI, TTY is not allocated in zuul
	echo $(SOURCE_BRANCH)
	$(CONTAINER_ENGINE) run --rm --pull=always \
		--env COV_REPORT \
		--env TEST_TARGET \
		--env COLOR \
		-v $(CURDIR):/src \
		-w /src \
		--security-opt label=disable \
		-v $(CURDIR)/files/packit-service.yaml:/root/.config/packit-service.yaml \
		$(TEST_IMAGE) make check "TEST_TARGET=$(TEST_TARGET)"

# This is my target so don't touch it! :) How to:
# * No dependencies - take care of them yourself
# * Make sure to set `command_handler: local`: there is no kube API in pristine containers
# * Make sure to `docker-compose up redis postgres`
# Features:
# * Can regen requre stuff (`TEST_TARGET=./tests_requre/openshift_integration/`)
# * Mounts your source code in the container
# * Mounts secrets in the container: make sure all are valid
# * Can touch redis and psql
check-in-container-tomas:
	@# don't use -ti here in CI, TTY is not allocated in zuul
	$(CONTAINER_ENGINE) run --rm \
		-v $(CURDIR):/src \
		-v $(CURDIR)/packit_service:/usr/local/lib/python3.7/site-packages/packit_service:ro,z \
		-v $(CURDIR)/secrets/dev/packit-service.yaml:/home/packit/.config/packit-service.yaml:ro,z \
		-v $(CURDIR)/secrets/dev/fedora.keytab:/secrets/fedora.keytab:ro,z \
		-v $(CURDIR)/secrets/dev/private-key.pem:/secrets/private-key.pem:ro,z \
		-v $(CURDIR)/secrets/dev/fullchain.pem:/secrets/fullchain.pem:ro,z \
		-v $(CURDIR)/secrets/dev/privkey.pem:/secrets/privkey.pem:ro,z \
		-w /src \
		--security-opt label=disable \
		-v $(CURDIR)/files/packit-service.yaml:/root/.config/packit-service.yaml \
		-v $(CURDIR)/tests_requre/openshift_integration/test_data/:/tmp/test_data/ \
		--network packit-service_default \
		$(TEST_IMAGE) make check "TEST_TARGET=$(TEST_TARGET)"

# deploy a pod with tests and run them
check-inside-openshift: service worker
	@# http://timmurphy.org/2015/09/27/how-to-get-a-makefile-directory-path/
	@# sadly the hostPath volume doesn't work:
	@#   Invalid value: "hostPath": hostPath volumes are not allowed to be used
	@#   username system:admin is invalid for basic auth
	@#-p PACKIT_SERVICE_SRC_LOCAL_PATH=$(dir $(realpath $(firstword $(MAKEFILE_LIST))))
	ANSIBLE_STDOUT_CALLBACK=debug $(AP) -K -e path_to_secrets=$(PATH_TO_SECRETS) files/deployment.yaml
	ANSIBLE_STDOUT_CALLBACK=debug $(AP) files/check-inside-openshift.yaml

check-inside-openshift-zuul:
	ANSIBLE_STDOUT_CALLBACK=debug $(AP) files/check-inside-openshift.yaml

setup-inside-toolbox:
	@if [[ ! -e /run/.toolboxenv ]]; then \
		echo "Not running in a toolbox!"; \
		exit 1; \
	fi
	dnf install -y ansible
	SOURCE_BRANCH=$(SOURCE_BRANCH) ANSIBLE_STDOUT_CALLBACK=debug $(AP) files/setup-toolbox.yaml

check-inside-toolbox:
	bash files/test_in_toolbox.sh packit_service

requre-purge-files:
	pre-commit run requre-purge --all-files --verbose --hook-stage manual
