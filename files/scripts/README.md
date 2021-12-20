# Allowlisting an account

You need to login to our OpenShift cluster and list all pods.

Approving user who is waiting on the allowlist:

```
$ oc exec packit-worker-0 allowlist.py approve <path_to_namespace>
```

Once you approve the account, go to [packit-service/notifications](https://github.com/packit/notifications/issues) and close the issue with corresponding `<path_to_namespace>`.

Removing user from the allowlist:

```
$ oc exec packit-worker-0 allowlist.py remove <path_to_namespace>
```

Show all pending requests:

```
$ oc exec packit-worker-0 allowlist.py waiting
```
