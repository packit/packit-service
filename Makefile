TEST_TARGET := ./tests/
PACKIT_IMAGE := docker.io/usercont/packit-service:master

build: recipe.yaml files/install-rpm-packages.yaml
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

prepare-check:
	ansible-playbook -b -K -i inventory-local -c local ./recipe-tests.yaml

check:
	tox
