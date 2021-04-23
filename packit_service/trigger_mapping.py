# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from packit.config import JobType


def are_job_types_same(first: JobType, second: JobType) -> bool:
    """
    We need to treat `build` alias in a special way.
    """
    return first == second or {first, second} == {JobType.build, JobType.copr_build}
