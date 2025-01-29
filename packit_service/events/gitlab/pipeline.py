# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from typing import Optional

from .abstract import GitlabEvent


class Pipeline(GitlabEvent):
    def __init__(
        self,
        project_url: str,
        project_name: str,
        pipeline_id: int,
        git_ref: str,
        status: str,
        detailed_status: str,
        commit_sha: str,
        source: str,
        merge_request_url: Optional[str],
    ):
        super().__init__(project_url=project_url)
        self.project_name = project_name
        self.pipeline_id = pipeline_id
        self.git_ref = git_ref
        self.status = status
        self.detailed_status = detailed_status
        self.commit_sha = commit_sha
        self.source = source
        self.merge_request_url = merge_request_url

    @classmethod
    def event_type(cls) -> str:
        return "gitlab.pipeline.Pipeline"
