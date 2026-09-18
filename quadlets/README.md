# Setting up quadlet environment

> [!CAUTION]
> This setup is still a work in progress

Packit can be deployed as quadlet services. The goal is to make it possible to
simply run

```console
$ systemctl --user daemon-reload
$ systemctl --user start packit-service
```

and be out to the races

## Various environment setups

The quadlets are organized into multiple drop-in configuration folders that get
merged:

- `base`: The main quadlet definitions shared across all environments
- `dev`: Development environment using the current state of the git repo and
  that _should_ pick up live edits to the sources
- `prod`: Production environment used/mimicking the current environment
  deployed
- `local`: Additional drop-in files setup locally

In order to use these, you can either

1. Copy the drop-in folders into the [quadlet search path]
2. Create a `/etc/systemd/user-environment-generators/10-packit-service`
   executable file with content such as

```bash
PACKIT_SERVICE_ROOT=/path/to/packit-service
# Enable dev environment (along with mandatory base drop-ins)
echo "QUADLET_UNIT_DIRS=$PACKIT_SERVICE_ROOT/quadlets/dev:$PACKIT_SERVICE_ROOT/quadlets/base:$QUADLET_UNIT_DIRS"
```

[quadlet search path]: https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html#podman-rootless-unit-search-path

## Additional manual configuration

See the notes in each `base/*/10-*-secrets.conf` drop-in files for additional
setup steps that you are expected to do. Create equivalent drop-in files in
your `local` path.
