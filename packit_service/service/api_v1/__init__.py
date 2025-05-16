# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from .healthz import router as healthz_router
from .system import router as system_router

routers = [healthz_router, system_router]
