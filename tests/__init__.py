# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from packit.utils import set_logging

# debug logs from packit_service while testing
set_logging("packit_service", level=10)
set_logging("packit", level=10)
