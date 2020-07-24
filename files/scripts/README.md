# Whitelisting an account

You need to login to our OpenShift cluster and list all pods.

Approving user who is waiting on the whitelist:

```
$ oc exec packit-worker-0 python3 /src/files/scripts/whitelist.py approve <github_account>
```

Once you approve the account, go to [packit-service/notifications](https://github.com/packit-service/notifications/issues) and close the issue with corresponding `<github_account>`.

Removing user from the whitelist:

```
$ oc exec packit-worker-0 python3 /src/files/scripts/whitelist.py remove <github_account>
```

Show all pending requests:

```
$ oc exec packit-worker-0 python3 /src/files/scripts/whitelist.py waiting
```
