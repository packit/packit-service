#!/usr/bin/env python3
import os
import subprocess

WARNING_MSG = (
    "Your branch is not up to date with upstream/master. \n"
    "SHA of the last commit of upstream/master: {upstream}\n"
    "Please, rebase!"
)


def main():
    path = os.path.dirname(os.path.abspath(__file__))

    last_commit_subject = subprocess.run(
        ["git", "log", "--format=%s", "-1"], capture_output=True, cwd=path
    ).stdout.decode()
    print(f"Last commit subject: {last_commit_subject}")

    if "Merge commit" in last_commit_subject:
        print("Zuul merged the master -- rebase is needed.")
        return 2

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

    print(f"Upstream hash: {upstream_hash}\n" f"Local hashes: {local_hashes}\n")

    if upstream_hash in local_hashes:
        return 0
    print(WARNING_MSG.format(upstream=upstream_hash))
    return 1


if __name__ == "__main__":
    exit(main())
