SERVICE_IMAGE ?= docker.io/usercont/packit-service:dev
WORKER_IMAGE ?= docker.io/usercont/packit-service-worker:dev
WORKER_IMAGE_PROD ?= docker.io/usercont/packit-service-worker:prod
TEST_IMAGE ?= packit-service-tests
TEST_TARGET ?= ./tests/unit ./tests/integration/
CONTAINER_ENGINE ?= docker
ANSIBLE_PYTHON ?= /usr/bin/python3
AP ?= ansible-playbook -vv -c local -i localhost, -e ansible_python_interpreter=$(ANSIBLE_PYTHON)

service: files/install-deps.yaml files/recipe.yaml
	$(CONTAINER_ENGINE) build --rm -t $(SERVICE_IMAGE) .

worker: files/install-deps-worker.yaml files/recipe-worker.yaml
	$(CONTAINER_ENGINE) build --rm -t $(WORKER_IMAGE) -f Dockerfile.worker .

# This is for cases when you want to deploy into production and don't want to wait for dockerhub
# Make sure you have latest docker.io/usercont/packit:prod prior to running this
worker-prod: files/install-deps-worker.yaml files/recipe-worker.yaml
	$(CONTAINER_ENGINE) build --rm -t $(WORKER_IMAGE_PROD) -f Dockerfile.worker.prod .
worker-prod-push: worker-prod
	$(CONTAINER_ENGINE) push $(WORKER_IMAGE_PROD)

check:
	find . -name "*.pyc" -exec rm {} \;
	PYTHONPATH=$(CURDIR) PYTHONDONTWRITEBYTECODE=1 python3 -m pytest --color=yes --verbose --showlocals --cov=packit_service --cov-report=term-missing $(TEST_TARGET)

# first run 'make worker'
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
check-inside-openshift: worker test_image
	@# http://timmurphy.org/2015/09/27/how-to-get-a-makefile-directory-path/
	@# sadly the hostPath volume doesn't work:
	@#   Invalid value: "hostPath": hostPath volumes are not allowed to be used
	@#   username system:admin is invalid for basic auth
	@#-p PACKIT_SERVICE_SRC_LOCAL_PATH=$(dir $(realpath $(firstword $(MAKEFILE_LIST))))
	ANSIBLE_STDOUT_CALLBACK=debug $(AP) -e path_to_secrets=$(PATH_TO_SECRETS) files/test-in-openshift-secrets.yaml
	ANSIBLE_STDOUT_CALLBACK=debug $(AP) files/check-inside-openshift.yaml

check-inside-openshift-zuul: test_image
	ANSIBLE_STDOUT_CALLBACK=debug $(AP) files/check-inside-openshift.yaml


# this target is expected to run within an openshift pod
check-within-openshift:
	/src-packit-service/files/setup_env_in_openshift.sh
	pytest-3 -k test_update
