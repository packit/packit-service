# Allowlisting an account

You need to login to our OpenShift cluster and list all pods.

Approving user who is waiting on the allowlist:

```
$ oc exec packit-worker-0 python3 /src/files/scripts/allowlist.py approve <github_account>
```

Once you approve the account, go to [packit-service/notifications](https://github.com/packit/notifications/issues) and close the issue with corresponding `<github_account>`.

Removing user from the allowlist:

```
$ oc exec packit-worker-0 python3 /src/files/scripts/allowlist.py remove <github_account>
```

Show all pending requests:

```
$ oc exec packit-worker-0 python3 /src/files/scripts/allowlist.py waiting
```
