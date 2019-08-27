TEST_TARGET := ./tests/
PACKIT_IMAGE := docker.io/usercont/packit-service:master
TEST_IMAGE := packit-service-tests

build: files/install-rpm-packages.yaml files/recipe.yaml
	docker build --rm -t $(PACKIT_IMAGE) .

# we can't use rootless podman here b/c we can't mount ~/.ssh inside (0400)
run:
	docker run -it --rm --net=host \
		-u 1000 \
		-e FLASK_ENV=development \
		-e PAGURE_USER_TOKEN \
		-e PAGURE_FORK_TOKEN \
		-e GITHUB_TOKEN \
		-w /src \
		-v ~/.ssh/:/home/packit/.ssh/:Z \
		-v $(CURDIR):/src:Z \
		$(PACKIT_IMAGE) bash

run-fedmsg:
	docker run -it --rm --net=host \
		-u 1000 \
		-w /src \
		-v ~/.ssh/:/home/packit/.ssh/:Z \
		-v $(CURDIR):/src:Z \
		$(PACKIT_IMAGE) bash

check:
	find . -name "*.pyc" -exec rm {} \;
	PYTHONPATH=$(CURDIR) PYTHONDONTWRITEBYTECODE=1 python3 -m pytest --color=yes --verbose --showlocals --cov=packit_service --cov-report=term-missing $(TEST_TARGET)

test_image: files/install-rpm-packages.yaml files/recipe-tests.yaml
	podman build --rm -t $(TEST_IMAGE) -f Dockerfile.tests .

check_in_container: test_image
	podman run --rm -ti \
		-v $(CURDIR):/src \
		-w /src \
		--security-opt label=disable \
		-v $(CURDIR)/files/packit-service.yaml:/root/.config/packit-service.yaml \
		$(TEST_IMAGE) make check
