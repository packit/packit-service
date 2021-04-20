# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.

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
import typing

from marshmallow import ValidationError, fields, post_load, Schema

from packit.schema import UserConfigSchema
from packit_service.config import Deployment, ServiceConfig, ProjectToSync


class DeploymentField(fields.Field):
    def _serialize(self, value: typing.Any, attr: str, obj: typing.Any, **kwargs):
        raise NotImplementedError

    def _deserialize(
        self,
        value: typing.Any,
        attr: typing.Optional[str],
        data: typing.Optional[typing.Mapping[str, typing.Any]],
        **kwargs,
    ) -> Deployment:
        if not isinstance(value, str):
            raise ValidationError("Invalid data provided. str required")

        return Deployment(value)


class ProjectToSyncSchema(Schema):
    """
    Schema for projects to sync.
    """

    forge = fields.String(required=True)
    repo_namespace = fields.String(required=True)
    repo_name = fields.String(required=True)
    branch = fields.String(required=True)
    dg_repo_name = fields.String(required=True)
    dg_branch = fields.String(required=True)

    @post_load
    def make_instance(self, data, **_):
        return ProjectToSync(**data)


class ServiceConfigSchema(UserConfigSchema):
    deployment = DeploymentField(required=True)
    webhook_secret = fields.String()
    testing_farm_secret = fields.String()
    testing_farm_api_url = fields.String()
    fas_password = fields.String(default="")
    validate_webhooks = fields.Bool(default=False)
    bugzilla_url = fields.String(default="")
    bugzilla_api_key = fields.String(default="")
    pr_accepted_labels = fields.List(fields.String())
    admins = fields.List(fields.String())
    server_name = fields.String()
    gitlab_webhook_tokens = fields.List(fields.String())
    gitlab_token_secret = fields.String()
    enabled_private_namespaces = fields.List(fields.String())
    projects_to_sync = fields.List(fields.Nested(ProjectToSyncSchema), missing=None)
    dashboard_url = fields.String()

    @post_load
    def make_instance(self, data, **kwargs):
        return ServiceConfig(**data)
