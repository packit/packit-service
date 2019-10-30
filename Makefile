SERVICE_IMAGE := docker.io/usercont/packit-service
WORKER_IMAGE := docker.io/usercont/packit-service-worker
WORKER_PROD_IMAGE := docker.io/usercont/packit-service-worker:prod
TEST_IMAGE := packit-service-tests
TEST_TARGET := ./tests/unit ./tests/integration/
CONTAINER_ENGINE := docker
ANSIBLE_PYTHON := /usr/bin/python3
AP := ansible-playbook -vv -c local -i localhost, -e ansible_python_interpreter=$(ANSIBLE_PYTHON)

build: files/install-deps.yaml files/recipe.yaml
	docker build --rm -t $(SERVICE_IMAGE) .

worker: CONTAINER_ENGINE ?= docker
worker: files/install-deps-worker.yaml files/recipe-worker.yaml
	$(CONTAINER_ENGINE) build --rm -t $(WORKER_IMAGE) -f Dockerfile.worker .

# this is for cases when you want to deploy into production and don't want to wait for dockerhub
worker-prod: files/install-deps-worker.yaml files/recipe-worker.yaml
	docker build --rm -t $(WORKER_PROD_IMAGE) -f Dockerfile.worker.prod .
worker-prod-push: worker-prod
	docker push $(WORKER_PROD_IMAGE)

# we can't use rootless podman here b/c we can't mount ~/.ssh inside (0400)
run-worker:
	docker run -it --rm --net=host \
		-u 1000 \
		-e FLASK_ENV=development \
		-e PAGURE_USER_TOKEN \
		-e PAGURE_FORK_TOKEN \
		-e GITHUB_TOKEN \
		-w /src \
		-v ~/.ssh/:/home/packit/.ssh/:Z \
		-v $(CURDIR):/src:Z \
		$(WORKER_IMAGE) bash

run-fedmsg:
	docker run -it --rm --net=host \
		-u 1000 \
		-w /src \
		-v ~/.ssh/:/home/packit/.ssh/:Z \
		-v $(CURDIR):/src:Z \
		$(WORKER_IMAGE) bash

check:
	find . -name "*.pyc" -exec rm {} \;
	PYTHONPATH=$(CURDIR) PYTHONDONTWRITEBYTECODE=1 python3 -m pytest --color=yes --verbose --showlocals --cov=packit_service --cov-report=term-missing $(TEST_TARGET)

test_image: CONTAINER_ENGINE ?= docker
test_image: files/install-deps.yaml files/recipe-tests.yaml
	$(CONTAINER_ENGINE) build --rm -t $(TEST_IMAGE) -f Dockerfile.tests .

check_in_container: test_image
	@# don't use -ti here in CI, TTY is not allocated in zuul
	$(CONTAINER_ENGINE) run --rm \
		-v $(CURDIR):/src-packit-service \
		-w /src-packit-service \
		--security-opt label=disable \
		-v $(CURDIR)/files/packit-service.yaml:/root/.config/packit-service.yaml \
		$(TEST_IMAGE) make check

# deploy a pod with tests and run them
check-inside-openshift: CONTAINER_ENGINE=docker
check-inside-openshift: test_image
	@# http://timmurphy.org/2015/09/27/how-to-get-a-makefile-directory-path/
	@# sadly the hostPath volume doesn't work:
	@#   Invalid value: "hostPath": hostPath volumes are not allowed to be used
	@#   username system:admin is invalid for basic auth
	@#-p PACKIT_SERVICE_SRC_LOCAL_PATH=$(dir $(realpath $(firstword $(MAKEFILE_LIST))))
	$(AP) files/test-in-openshift-secrets.yaml
	$(AP) files/check-inside-openshift.yaml


# this target is expected to run within an openshift pod
check-within-openshift:
	/src-packit-service/files/setup_env_in_openshift.sh
	pytest-3 -k test_update
