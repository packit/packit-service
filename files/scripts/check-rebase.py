#!/usr/bin/env python3
import subprocess
import os

WARNING_MSG = (
    "Your branch is not up to date with upstream/master. \n"
    "SHA of the last commit of upstream/master: {upstream}\n"
    "Please, rebase!"
)


def main():
    path = os.path.dirname(os.path.abspath(__file__))
    local_hashes = (
        subprocess.run(
            ["git", "log", "--max-count=100", "--format=%H"],
            capture_output=True,
            cwd=path,
        )
        .stdout.decode()
        .split()
    )

    upstream_hash = (
        subprocess.run(
            [
                "git",
                "ls-remote",
                "git://github.com/packit-service/packit-service.git",
                "HEAD",
            ],
            capture_output=True,
            cwd=path,
        )
        .stdout.decode()
        .split()[0]
    )

    if upstream_hash in local_hashes:
        return 0
    print(WARNING_MSG.format(upstream=upstream_hash))
    return 1


if __name__ == "__main__":
    exit(main())
