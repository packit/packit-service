# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

# from collections import OrderedDict
# from datetime import datetime, timedelta, timezone
# from http import HTTPStatus
# from logging import getLogger
# from typing import Any

# from cachetools.func import ttl_cache

# from flask import request, escape
# from flask_restx import Namespace, Resource

# from packit_service.models import (
#     CoprBuildGroupModel,
#     GitProjectModel,
#     ProjectEventModelType,
#     KojiBuildGroupModel,
#     SRPMBuildModel,
#     SyncReleaseModel,
#     TFTTestRunGroupModel,
#     VMImageBuildTargetModel,
# )
# from packit_service.service.api.utils import response_maker

# logger = getLogger("packit_service")

# TODO: Migrate properly
# architecture_ns = Namespace(
#     "architecture",
#     description="Provides API endpoints related to architecture of Packit"
# )


# @architecture_ns.route("/diagram.svg")
# class ArchitectureDiagram(Resource):
#     def get(self):
#         system_info = make_response(f"{API_URL}/system").json
#         system_info["packit_dashboard"] = dict(commit=os.environ.get("VITE_GIT_SHA"))
#         system_info["packit_worker"] = system_info["packit_service"]
#         project_ref_mapping = {
#             project.replace("-", "_") + "_ref": info.get("commit") or info.get("version")
#             for project, info in system_info.items()
#         }

#         return render_template("architecture-red.svg", **project_ref_mapping)
