# MIT License
#
# Copyright (c) 2019 Red Hat, Inc.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
from flask import Blueprint
from flask_restplus import Api

from packit_service.service.api.copr_builds import ns as copr_builds_ns
from packit_service.service.api.healthz import ns as healthz_ns
from packit_service.service.api.installations import ns as installations_ns
from packit_service.service.api.testing_farm import ns as testing_farm_ns
from packit_service.service.api.webhooks import ns as webhooks_ns
from packit_service.service.api.whitelist import ns as whitelist_ns

# https://flask-restplus.readthedocs.io/en/stable/scaling.html
blueprint = Blueprint("api", __name__, url_prefix="/api")
api = Api(
    app=blueprint,
    version="1.0",
    title="Packit Service API",
    description="https://packit.dev/packit-as-a-service",
)

api.add_namespace(copr_builds_ns)
api.add_namespace(healthz_ns)
api.add_namespace(installations_ns)
api.add_namespace(testing_farm_ns)
api.add_namespace(webhooks_ns)
api.add_namespace(whitelist_ns)
