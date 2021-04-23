# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from pkg_resources import DistributionNotFound, get_distribution

try:
    __version__ = get_distribution(__name__).version
except DistributionNotFound:
    # package is not installed
    __version__ = None
