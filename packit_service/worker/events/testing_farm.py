# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
from datetime import datetime
from typing import Optional, Dict

from ogr.abstract import GitProject
from ogr.services.pagure import PagureProject

from packit_service.models import (
    TestingFarmResult,
    AbstractTriggerDbType,
    PullRequestModel,
    TFTTestRunTargetModel,
)
from packit_service.worker.events.event import AbstractForgeIndependentEvent


class TestingFarmResultsEvent(AbstractForgeIndependentEvent):
    def __init__(
        self,
        pipeline_id: str,
        result: TestingFarmResult,
        compose: str,
        summary: str,
        log_url: str,
        copr_build_id: str,
        copr_chroot: str,
        commit_sha: str,
        project_url: str,
        created: datetime,
        identifier: Optional[str] = None,
    ):
        super().__init__(project_url=project_url)
        self.pipeline_id: str = pipeline_id
        self.result: TestingFarmResult = result
        self.compose: str = compose
        self.summary: str = summary
        self.log_url: str = log_url
        self.copr_build_id: str = copr_build_id
        self.copr_chroot: str = copr_chroot
        self.commit_sha: str = commit_sha
        self.created: datetime = created
        self.identifier: Optional[str] = identifier

        # Lazy properties
        self._pr_id: Optional[int] = None
        self._db_trigger: Optional[AbstractTriggerDbType] = None

    @property
    def pr_id(self) -> Optional[int]:
        if not self._pr_id and isinstance(self.db_trigger, PullRequestModel):
            self._pr_id = self.db_trigger.pr_id
        return self._pr_id

    def get_dict(self, default_dict: Optional[Dict] = None) -> dict:
        result = super().get_dict()
        result["result"] = result["result"].value
        result["pr_id"] = self.pr_id
        return result

    def get_db_trigger(self) -> Optional[AbstractTriggerDbType]:
        run_model = TFTTestRunTargetModel.get_by_pipeline_id(
            pipeline_id=self.pipeline_id
        )
        return run_model.get_trigger_object() if run_model else None

    def get_base_project(self) -> Optional[GitProject]:
        if self.pr_id is not None:
            if isinstance(self.project, PagureProject):
                pull_request = self.project.get_pr(pr_id=self.pr_id)
                return self.project.service.get_project(
                    namespace=self.project.namespace,
                    username=pull_request.author,
                    repo=self.project.repo,
                    is_fork=True,
                )
            else:
                return None  # With Github app, we cannot work with fork repo
        return self.project
