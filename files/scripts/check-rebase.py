#!/usr/bin/env python3
import subprocess
import os

WARNING_MSG = (
    "Your branch is not up to date with upstream/master. \n"
    "SHA of the last commit of upstream/master: {first}\n"
    "SHA of the last commit of upstream/master in your branch: {second}. \n"
    "Please, rebase!"
)


def main():
    path = os.path.dirname(os.path.abspath(__file__))
    local_upstream_hash = (
        subprocess.run(
            ["git", "log", "upstream/master", "-1", "--format=%H"],
            capture_output=True,
            cwd=path,
        )
        .stdout.decode()
        .rstrip()
    )

    upstream_hash = subprocess.run(
        ["git", "ls-remote", "upstream", "HEAD"], capture_output=True, cwd=path
    ).stdout.decode()[: len(local_upstream_hash)]

    if local_upstream_hash == upstream_hash:
        return 0
    print(WARNING_MSG.format(first=upstream_hash, second=local_upstream_hash))
    return 1


if __name__ == "__main__":
    exit(main())
