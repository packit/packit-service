# Packit Service [![Build Status](https://zuul-ci.org/gated.svg)](https://softwarefactory-project.io/zuul/t/local/builds?project=packit-service/packit-service)

A service which helps you integrate your upstream GitHub project into Fedora
ecosystem. Think of it as [packit](https://github.com/packit/packit) on
steroids.

## Current status

For the run-down of the planned work, please see the task-list below.

- [x] Packit service reacts to Github webhooks.
- [x] Packit service is scalable. (using celery and running on OpenShift)
- [x] Packit service is secure. (by running things in a [sandbox](https://github.com/packit/sandcastle/))
- [x] Packit Service GitHub app is placed in [GitHub marketplace](https://github.com/marketplace/packit-as-a-service).
- [ ] New upstream releases can be proposed as pull requests downstream.

## More info

If you'd like to know more about packit, please check:

- Our website: [packit.dev](https://packit.dev/)
- GitHub project for packit tool: [packit-service/packit](https://github.com/packit/packit)
- Hacking on packit service: [CONTRIBUTING.md](/CONTRIBUTING.md)
