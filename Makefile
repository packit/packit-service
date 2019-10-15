SERVICE_IMAGE := docker.io/usercont/packit-service
WORKER_IMAGE := docker.io/usercont/packit-service-worker
WORKER_PROD_IMAGE := docker.io/usercont/packit-service-worker:prod
TEST_IMAGE := packit-service-tests
TEST_TARGET := ./tests/integration/test_copr.py

build: files/install-deps.yaml files/recipe.yaml
	docker build --rm -t $(SERVICE_IMAGE) .

worker: files/install-deps-worker.yaml files/recipe-worker.yaml
	docker build --rm -t $(WORKER_IMAGE) -f Dockerfile.worker .

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

test_image: files/install-deps.yaml files/recipe-tests.yaml
	docker build --rm -t $(TEST_IMAGE) -f Dockerfile.tests .

check_in_container: test_image
	docker run --rm -ti \
		-v $(CURDIR):/src \
		-w /src \
		--security-opt label=disable \
		-v $(CURDIR)/files/packit-service.yaml:/root/.config/packit-service.yaml \
		$(TEST_IMAGE) make check
