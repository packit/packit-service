# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from flask import Blueprint
from flask_restx import Api

from packit_service.service.api.allowlist import ns as allowlist_ns
from packit_service.service.api.bodhi_updates import ns as bodhi_updates_ns
from packit_service.service.api.copr_builds import ns as copr_builds_ns
from packit_service.service.api.healthz import ns as healthz_ns
from packit_service.service.api.installations import ns as installations_ns
from packit_service.service.api.koji_builds import koji_builds_ns
from packit_service.service.api.koji_tag_requests import koji_tag_requests_ns
from packit_service.service.api.osh_scans import ns as osh_scans_ns
from packit_service.service.api.projects import ns as projects_ns
from packit_service.service.api.propose_downstream import ns as propose_downstream_ns
from packit_service.service.api.pull_from_upstream import ns as pull_from_upstream_ns
from packit_service.service.api.runs import ns as runs_ns
from packit_service.service.api.srpm_builds import ns as srpm_builds_ns
from packit_service.service.api.system import ns as system_ns
from packit_service.service.api.testing_farm import ns as testing_farm_ns
from packit_service.service.api.usage import usage_ns
from packit_service.service.api.webhooks import ns as webhooks_ns

# https://flask-restplus.readthedocs.io/en/stable/scaling.html
blueprint = Blueprint("api", __name__, url_prefix="/api")
api = Api(
    app=blueprint,
    version="1.0",
    title="Packit Service API",
    description="https://packit.dev/",
)

api.add_namespace(copr_builds_ns)
api.add_namespace(projects_ns)
api.add_namespace(healthz_ns)
api.add_namespace(installations_ns)
api.add_namespace(testing_farm_ns)
api.add_namespace(webhooks_ns)
api.add_namespace(allowlist_ns)
api.add_namespace(koji_builds_ns)
api.add_namespace(koji_tag_requests_ns)
api.add_namespace(srpm_builds_ns)
api.add_namespace(runs_ns)
api.add_namespace(propose_downstream_ns)
api.add_namespace(usage_ns)
api.add_namespace(pull_from_upstream_ns)
api.add_namespace(system_ns)
api.add_namespace(bodhi_updates_ns)
api.add_namespace(osh_scans_ns)
