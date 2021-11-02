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
    TFTTestRunModel,
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
    ):
        super().__init__(project_url=project_url)
        self.pipeline_id = pipeline_id
        self.result = result
        self.compose = compose
        self.summary = summary
        self.log_url = log_url
        self.copr_build_id = copr_build_id
        self.copr_chroot = copr_chroot
        self.commit_sha: str = commit_sha
        self.created: datetime = created

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
        result.pop("_db_trigger")
        return result

    @property
    def db_trigger(self) -> Optional[AbstractTriggerDbType]:
        if not self._db_trigger:
            run_model = TFTTestRunModel.get_by_pipeline_id(pipeline_id=self.pipeline_id)
            if run_model:
                self._db_trigger = run_model.get_trigger_object()
        return self._db_trigger

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
