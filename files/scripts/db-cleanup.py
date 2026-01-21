#!/usr/bin/python3

# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
Script for manual database cleanup operations.

This script provides a CLI interface to the delete_old_data function
from packit_service.worker.database module for manual DB cleanup.
"""

import argparse
import sys

from packit_service.worker.database import delete_old_data


def main():
    """CLI entry point for database cleanup script."""
    parser = argparse.ArgumentParser(
        description="""\
Remove old data from the DB in order to speed up queries.

Set POSTGRESQL_* environment variables to define the DB URL.
See get_pg_url() for details.
""",
    )
    parser.add_argument(
        "age",
        type=str,
        nargs="?",
        default="1 year",
        help="Remove data older than this. For example: "
        "'1 year' or '6 months'. Defaults to '1 year'.",
    )

    args = parser.parse_args()

    try:
        delete_old_data(age=args.age)
        return 0
    except Exception as e:
        print(f"Error during cleanup: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
