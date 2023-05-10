# Allowlisting/denylisting an account

You need to login to our OpenShift cluster and list all pods. Use the `allowlist.py` script inside the worker pod to manipulate the allowlist.

## List pending namespaces

List all requests pending approval:

```
$ oc exec packit-worker-short-running-0 allowlist.py waiting
```

Use `oc exec -it ...` instead if you also want to approve a namespace from the waiting list and specify the number of namespace to approve.

## Manual approval

In order to add to the allowlist manually:

```
$ oc exec -it packit-worker-short-running-0 allowlist.py approve <path_to_namespace>
```

The `<path_to_namespace>` string should follow the same format which is used in the list of waiting requests, i.e. the domain should be included.
For example, for an organization/user `packit` at Github, `github.com/packit` should be used for the allowlist.
In order to add only a single repository to the allowlist, the `.git` suffix must explicitly be used, e.g. `github.com/packit/ogr.git`.
After approving, close the corresponding issue at [packit-service/notifications](https://github.com/packit/notifications/issues).

## List denied namespaces

List all denied namespaces:

```
$ oc exec packit-worker-short-running-0 allowlist.py denied
```

## Denying

Denying a user:

```
$ oc exec -it packit-worker-short-running-0 allowlist.py deny <path_to_namespace>
```

## Removal

Removing a user or from the allowlist:

```
$ oc exec -it packit-worker-short-running-0 allowlist.py remove <path_to_namespace>
```

# Cleaning up the database

This also requires logging in to the OpenShift cluster and selecting the right
project in order to be able to run the script.

Then run

```
$ oc exec packit-worker-long-running-0 db-cleanup.py
```

which removes all data older than a year from the database. It's possible to
remove even more, by specifying the maximum age of the data:

```
$ oc exec packit-worker-long-running-0 db-cleanup.py '6 months'
```
