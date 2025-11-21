# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import typing

from marshmallow import Schema, ValidationError, fields, post_load
from packit.config.common_package_config import Deployment
from packit.schema import UserConfigSchema

from packit_service.config import MRTarget, ProjectToSync, ServiceConfig


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


class MRTargetSchema(Schema):
    """
    Schema for MR targets to handle.

    repo: Regex string to be matched against the slug of a repo.
    branch: Regex string to be matched against the branch name.
    """

    repo = fields.String(missing=None)
    branch = fields.String(missing=None)

    @post_load
    def make_instance(self, data, **_):
        return MRTarget(**data)


class ServiceConfigSchema(UserConfigSchema):
    deployment = DeploymentField(required=True)
    webhook_secret = fields.String()
    testing_farm_secret = fields.String()
    testing_farm_api_url = fields.String()
    internal_testing_farm_secret = fields.String()
    fas_password = fields.String(default="")
    validate_webhooks = fields.Bool(default=False)
    admins = fields.List(fields.String())
    server_name = fields.String()
    gitlab_token_secret = fields.String()
    gitlab_mr_targets_handled = fields.List(fields.Nested(MRTargetSchema), missing=None)
    enabled_private_namespaces = fields.List(fields.String())
    enabled_projects_for_internal_tf = fields.List(fields.String())
    projects_to_sync = fields.List(fields.Nested(ProjectToSyncSchema), missing=None)
    dashboard_url = fields.String()
    koji_logs_url = fields.String()
    koji_web_url = fields.String()
    enabled_projects_for_srpm_in_copr = fields.List(fields.String())
    comment_command_prefix = fields.String()
    package_config_path_override = fields.String()
    command_handler_storage_class = fields.String(missing="gp2")
    appcode = fields.String()
    enabled_projects_for_fedora_ci = fields.List(fields.String())

    @post_load
    def make_instance(self, data, **kwargs):
        return ServiceConfig(**data)
