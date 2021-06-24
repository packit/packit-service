# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
from typing import Optional, Dict

from packit.config.package_config import get_package_config_from_repo

from packit_service.models import AbstractTriggerDbType, GitBranchModel
from packit_service.service.events.event import AbstractForgeIndependentEvent
from packit_service.service.events.enums import FedmsgTopic


class DistGitCommitEvent(AbstractForgeIndependentEvent):
    def __init__(
        self,
        topic: str,
        repo_namespace: str,
        repo_name: str,
        branch: str,
        project_url: str,
        dg_repo_namespace: str,
        dg_repo_name: str,
        dg_branch: str,
        dg_rev: str,
        dg_project_url: str,
    ):
        super().__init__()
        self.topic = FedmsgTopic(topic)
        self.repo_namespace = repo_namespace
        self.repo_name = repo_name
        self.branch = branch
        self.project_url = project_url
        self.identifier = branch
        self.dg_repo_namespace = dg_repo_namespace
        self.dg_repo_name = dg_repo_name
        self.dg_branch = dg_branch
        self.dg_rev = dg_rev
        self.dg_project_url = dg_project_url

        self._package_config = None
        self._db_trigger: Optional[AbstractTriggerDbType] = None

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["topic"] = result["topic"].value
        result.pop("_db_trigger")
        return result

    @property
    def package_config(self):
        if not self._package_config:
            self._package_config = get_package_config_from_repo(
                self.project, self.branch
            )
        return self._package_config

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        if not self._db_trigger:
            self._db_trigger = GitBranchModel.get_or_create(
                branch_name=self.dg_rev,
                namespace=self.dg_repo_namespace,
                repo_name=self.dg_repo_name,
                project_url=self.dg_project_url,
            )
        return self._db_trigger
