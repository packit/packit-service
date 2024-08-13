# true|false
PULL_BASE_IMAGE ?= true
SERVICE_IMAGE ?= quay.io/packit/packit-service:dev
WORKER_IMAGE ?= quay.io/packit/packit-worker:dev
TEST_IMAGE ?= quay.io/packit/packit-service-tests:stg
PACKIT_PATH ?= ../packit
# missing|always|never
PULL_TEST_IMAGE ?= missing
TEST_TARGET ?= ./tests/unit ./tests/integration/
CONTAINER_ENGINE ?= $(shell command -v podman 2> /dev/null || echo docker)
ANSIBLE_PYTHON ?= $(shell command -v /usr/bin/python3 2> /dev/null || echo /usr/bin/python2)
AP ?= ansible-playbook -vv -c local -i localhost, -e ansible_python_interpreter=$(ANSIBLE_PYTHON)
PATH_TO_SECRETS ?= $(CURDIR)/secrets/
COV_REPORT ?= --cov=packit_service --cov-report=term-missing
COLOR ?= yes
SOURCE_BRANCH ?= $(shell git branch --show-current)
CONTAINER_RUN_INTERACTIVE ?= -it
COMPOSE ?= docker-compose
MY_ID ?= `id -u`

service: files/install-deps.yaml files/recipe.yaml
	$(CONTAINER_ENGINE) build --rm \
		--pull=$(PULL_BASE_IMAGE) \
		-t $(SERVICE_IMAGE) \
		-f files/docker/Dockerfile \
		--build-arg SOURCE_BRANCH=$(SOURCE_BRANCH) \
		.

worker: files/install-deps-worker.yaml files/recipe-worker.yaml
	$(CONTAINER_ENGINE) build --rm \
		--pull=$(PULL_BASE_IMAGE) \
		-t $(WORKER_IMAGE) \
		-f files/docker/Dockerfile.worker \
		--build-arg SOURCE_BRANCH=$(SOURCE_BRANCH) \
		.

check:
	find . -name "*.pyc" -exec rm {} \;
	PYTHONPATH=$(CURDIR) PYTHONDONTWRITEBYTECODE=1 python3 -m pytest --color=$(COLOR) --verbose --showlocals $(COV_REPORT) $(TEST_TARGET)

# In most cases you don't need to build your test-image, the one in registry should be all you need.
build-test-image: files/install-deps-worker.yaml files/install-deps.yaml files/recipe-tests.yaml
	$(CONTAINER_ENGINE) build --rm \
		--pull=$(PULL_BASE_IMAGE) \
		-t $(TEST_IMAGE) \
		-f files/docker/Dockerfile.tests \
		--build-arg SOURCE_BRANCH=$(SOURCE_BRANCH) \
		.

build-revdep-test-image: build-test-image
	$(CONTAINER_ENGINE) build \
		-t $(TEST_IMAGE) \
		-f files/docker/Containerfile.revdep \
	    -v $(shell realpath $(PACKIT_PATH)):/var/packit:Z \
		.

# We use a test image pre-built (by Github action) from latest commit in main.
# The PULL_TEST_IMAGE specifies whether the image is downloaded before running tests in a container.
# Default is 'missing', which means that it's downloaded/updated ONLY if missing.
# Set PULL_TEST_IMAGE=always to pull/update the test image before running tests.
check-in-container:
	@# don't use -ti here in CI, TTY is not allocated in zuul
	echo $(SOURCE_BRANCH)
	$(CONTAINER_ENGINE) run --rm $(CONTAINER_RUN_INTERACTIVE) \
		--pull="$(PULL_TEST_IMAGE)" \
		--env COV_REPORT \
		--env TEST_TARGET \
		--env COLOR \
		--env PUSHGATEWAY_ADDRESS= \
		-v $(CURDIR):/src:Z \
		-w /src \
		-v $(CURDIR)/files/packit-service.yaml:/root/.config/packit-service.yaml:Z \
		$(TEST_IMAGE) make check "TEST_TARGET=$(TEST_TARGET)"

# This is my target so don't touch it! :) How to:
# * No dependencies - take care of them yourself
# * Make sure to set `command_handler: local`: there is no kube API in pristine containers
# * Make sure to `docker-compose up redis postgres`
# Features:
# * Can regen requre stuff (`TEST_TARGET=./tests_openshift/openshift_integration/`)
# * Mounts your source code in the container
# * Mounts secrets in the container: make sure all are valid
# * Can touch redis and psql
check-in-container-tomas:
	@# don't use -ti here in CI, TTY is not allocated in zuul
	$(CONTAINER_ENGINE) run --rm \
		-v $(CURDIR):/src \
		-v $(CURDIR)/packit_service:/usr/local/lib/python3.7/site-packages/packit_service:ro,z \
		-v $(CURDIR)/secrets/packit/dev/packit-service.yaml:/home/packit/.config/packit-service.yaml:ro,z \
		-v $(CURDIR)/secrets/packit/dev/fedora.keytab:/secrets/fedora.keytab:ro,z \
		-v $(CURDIR)/secrets/packit/dev/private-key.pem:/secrets/private-key.pem:ro,z \
		-v $(CURDIR)/secrets/packit/dev/fullchain.pem:/secrets/fullchain.pem:ro,z \
		-v $(CURDIR)/secrets/packit/dev/privkey.pem:/secrets/privkey.pem:ro,z \
		-w /src \
		--security-opt label=disable \
		-v $(CURDIR)/files/packit-service.yaml:/root/.config/packit-service.yaml \
		-v $(CURDIR)/tests_openshift/openshift_integration/test_data/:/tmp/test_data/ \
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

# run all the pods needed by the service pod
# use docker-compose to update and run them
# (postgres and redis)
compose-for-db-up:
	$(COMPOSE) up --build --force-recreate -d service

# run alembic revision through another service pod
# run processes as *local host user* inside the pod
# preserve *local host user* files' uid inside the pod
# See docs/database/README.md
migrate-db: compose-for-db-up
	sleep 10 # service pod have to be up and running "alembic upgrade head"
	podman run --rm -ti --user $(MY_ID) --uidmap=$(MY_ID):0:1 --uidmap=0:1:999 \
	-e DEPLOYMENT=dev \
	-e REDIS_SERVICE_HOST=redis \
	-e POSTGRESQL_USER=packit \
	-e POSTGRESQL_PASSWORD=secret-password \
	-e POSTGRESQL_HOST=postgres \
	-e POSTGRESQL_DATABASE=packit \
	-v $(CURDIR)/alembic:/src/alembic:rw,z \
	-v $(CURDIR)/packit_service:/usr/local/lib/python3.9/site-packages/packit_service:ro,z \
	-v $(CURDIR)/secrets/packit/dev/packit-service.yaml:/home/packit/.config/packit-service.yaml:ro,z \
	-v $(CURDIR)/secrets/packit/dev/fullchain.pem:/secrets/fullchain.pem:ro,z \
	-v $(CURDIR)/secrets/packit/dev/privkey.pem:/secrets/privkey.pem:ro,z \
	--network packit-service_default \
	quay.io/packit/packit-service:dev alembic revision -m "$(CHANGE)" --autogenerate
	$(COMPOSE) down # stop previously started pods: service, postgres and redis

# run db tests using the network created by
# docker compose
check-db: build-test-image compose-for-db-up
	sleep 10 # service pod have to be up and running and all migrations have to been applied
	$(CONTAINER_ENGINE) run --rm -ti \
		-e DEPLOYMENT=dev \
		-e REDIS_SERVICE_HOST=redis \
		-e POSTGRESQL_USER=packit \
		-e POSTGRESQL_PASSWORD=secret-password \
		-e POSTGRESQL_HOST=postgres \
		-e POSTGRESQL_DATABASE=packit \
		--pull="$(PULL_TEST_IMAGE)" \
		--env COV_REPORT \
		--env TEST_TARGET \
		--env COLOR \
		-v $(CURDIR):/src:z \
		-v $(CURDIR)/files/packit-service.yaml:/root/.config/packit-service.yaml:z \
		-v $(CURDIR)/secrets/packit/dev/fullchain.pem:/secrets/fullchain.pem:ro,z \
		-v $(CURDIR)/secrets/packit/dev/privkey.pem:/secrets/privkey.pem:ro,z \
		-w /src \
		--network packit-service_default \
		$(TEST_IMAGE) make check "TEST_TARGET=tests_openshift/database tests_openshift/service"
		$(COMPOSE) down

# To install mermerd run:
#     go install github.com/KarnerTh/mermerd@latest
regenerate-db-diagram: compose-for-db-up
	sleep 10
	mermerd -c postgresql://packit:secret-password@localhost:5432 -s public --useAllTables -o alembic/diagram.mmd

.PHONY: build-revdep-test-image
