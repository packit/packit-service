SERVICE_IMAGE ?= docker.io/usercont/packit-service:dev
WORKER_IMAGE ?= docker.io/usercont/packit-service-worker:dev
WORKER_IMAGE_PROD ?= docker.io/usercont/packit-service-worker:prod
TEST_IMAGE ?= packit-service-tests
TEST_TARGET ?= ./tests/unit ./tests/integration/
CONTAINER_ENGINE ?= $(shell command -v podman 2> /dev/null || echo docker)
ANSIBLE_PYTHON ?= /usr/bin/python3
AP ?= ansible-playbook -vv -c local -i localhost, -e ansible_python_interpreter=$(ANSIBLE_PYTHON)
PATH_TO_SECRETS ?= $(CURDIR)/secrets/
COV_REPORT ?= term-missing
COLOR ?= yes

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

# in case of need change ip, this is default so whould work, also be sure docker(podman) is logged to local oc registry 'docker(podman) login 172.30.1.1:5000'
worker-local-push: worker
	$(CONTAINER_ENGINE) tag $(WORKER_IMAGE) 172.30.1.1:5000/packit-stg/packit-worker:dev
	$(CONTAINER_ENGINE) push 172.30.1.1:5000/packit-stg/packit-worker:dev --tls-verify=false

check:
	find . -name "*.pyc" -exec rm {} \;
	PYTHONPATH=$(CURDIR) PYTHONDONTWRITEBYTECODE=1 python3 -m pytest --color=$(COLOR) --verbose --showlocals --cov=packit_service --cov-report=$(COV_REPORT) $(TEST_TARGET)

# first run 'make worker'
test_image: files/install-deps.yaml files/recipe-tests.yaml
	$(CONTAINER_ENGINE) build --rm -t $(TEST_IMAGE) -f Dockerfile.tests .

check_in_container: test_image
	@# don't use -ti here in CI, TTY is not allocated in zuul
	$(CONTAINER_ENGINE) run --rm \
		--env COV_REPORT \
		--env TEST_TARGET \
		--env COLOR \
		-v $(CURDIR):/src-packit-service \
		-w /src-packit-service \
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
		-v $(CURDIR):/src-packit-service \
		-v $(CURDIR)/packit_service:/usr/local/lib/python3.7/site-packages/packit_service:ro,z \
		-v $(CURDIR)/secrets/dev/packit-service.yaml:/home/packit/.config/packit-service.yaml:ro,z \
		-v $(CURDIR)/secrets/dev/fedora.keytab:/secrets/fedora.keytab:ro,z \
		-v $(CURDIR)/secrets/dev/private-key.pem:/secrets/private-key.pem:ro,z \
		-v $(CURDIR)/secrets/dev/fullchain.pem:/secrets/fullchain.pem:ro,z \
		-v $(CURDIR)/secrets/dev/privkey.pem:/secrets/privkey.pem:ro,z \
		-w /src-packit-service \
		--security-opt label=disable \
		-v $(CURDIR)/files/packit-service.yaml:/root/.config/packit-service.yaml \
		-v $(CURDIR)/tests_requre/test_data/:/tmp/test_data/ \
		--network packit-service_default \
		-e REDIS_SERVICE_HOST=redis \
		$(TEST_IMAGE) make check "TEST_TARGET=$(TEST_TARGET)"

# deploy a pod with tests and run them
check-inside-openshift: worker test_image
	@# http://timmurphy.org/2015/09/27/how-to-get-a-makefile-directory-path/
	@# sadly the hostPath volume doesn't work:
	@#   Invalid value: "hostPath": hostPath volumes are not allowed to be used
	@#   username system:admin is invalid for basic auth
	@#-p PACKIT_SERVICE_SRC_LOCAL_PATH=$(dir $(realpath $(firstword $(MAKEFILE_LIST))))
	ANSIBLE_STDOUT_CALLBACK=debug $(AP) -K -e path_to_secrets=$(PATH_TO_SECRETS) files/deployment.yaml
	ANSIBLE_STDOUT_CALLBACK=debug $(AP) files/check-inside-openshift.yaml

check-inside-openshift-zuul: test_image
	ANSIBLE_STDOUT_CALLBACK=debug $(AP) files/check-inside-openshift.yaml
