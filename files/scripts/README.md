# Allowlisting an account

You need to login to our OpenShift cluster and list all pods. Use the `allowlist.py` script inside the worker pod to manipulate the allowlist.

List all requests pending approval:

```
$ oc exec packit-worker-0 allowlist.py waiting
```

Use `oc exec -it ...` instead if you also want to approve a namespace from the waiting list and specify the number of namespace to approve. In order to add to the allowlist manually:

```
$ oc exec -it packit-worker-0 allowlist.py approve <path_to_namespace>
```

The `<path_to_namespace>` string should follow the same format which is used in the list of waiting requests, i.e. the domain should be included.
For example, for an organization/user `packit` at Github, `github.com/packit` should be used for the allowlist.
In order to add only a single repository to the allowlist, the `.git` suffix must explicitly be used, e.g. `github.com/packit/ogr.git`.
After approving, close the corresponding issue at [packit-service/notifications](https://github.com/packit/notifications/issues).

Removing a user or from the allowlist:

```
$ oc exec packit-worker-0 allowlist.py remove <path_to_namespace>
```
