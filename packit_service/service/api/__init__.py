# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from flask import Blueprint
from flask_restx import Api

from packit_service.service.api.copr_builds import ns as copr_builds_ns
from packit_service.service.api.healthz import ns as healthz_ns
from packit_service.service.api.installations import ns as installations_ns
from packit_service.service.api.koji_builds import koji_builds_ns
from packit_service.service.api.projects import ns as projects_ns
from packit_service.service.api.srpm_builds import ns as srpm_builds_ns
from packit_service.service.api.testing_farm import ns as testing_farm_ns
from packit_service.service.api.webhooks import ns as webhooks_ns
from packit_service.service.api.allowlist import ns as allowlist_ns

# https://flask-restplus.readthedocs.io/en/stable/scaling.html
blueprint = Blueprint("api", __name__, url_prefix="/api")
api = Api(
    app=blueprint,
    version="1.0",
    title="Packit Service API",
    description="https://packit.dev/packit-as-a-service",
)

api.add_namespace(copr_builds_ns)
api.add_namespace(projects_ns)
api.add_namespace(healthz_ns)
api.add_namespace(installations_ns)
api.add_namespace(testing_farm_ns)
api.add_namespace(webhooks_ns)
api.add_namespace(allowlist_ns)
api.add_namespace(koji_builds_ns)
api.add_namespace(srpm_builds_ns)
