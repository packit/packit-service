# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
import base64
import gzip
import json
import logging
import re
import tempfile
from os import getenv
from os.path import basename
from pathlib import Path
from typing import Optional

from ogr.services.github import GithubProject
from packit.config import (
    JobConfig,
    JobConfigTriggerType,
    JobType,
)
from packit.utils import run_command

from packit_service.constants import (
    OPEN_SCAN_HUB_FEATURE_DESCRIPTION,
)
from packit_service.models import (
    BuildStatus,
    CoprBuildTargetModel,
    SRPMBuildModel,
)
from packit_service.service.urls import get_copr_build_info_url
from packit_service.utils import (
    download_file,
)
from packit_service.worker.helpers.build import CoprBuildJobHelper
from packit_service.worker.reporting import BaseCommitStatus

logger = logging.getLogger(__name__)


class OpenScanHubHelper:
    def __init__(
        self,
        copr_build_helper: CoprBuildJobHelper,
        build: CoprBuildTargetModel,
    ):
        self.build = build
        self.copr_build_helper = copr_build_helper

    @staticmethod
    def osh_disabled() -> bool:
        disabled = getenv("DISABLE_OPENSCANHUB", "False").lower() in (
            "true",
            "t",
            "yes",
            "y",
            "1",
        )
        if disabled:
            logger.info("OpenScanHub disabled via env var.")
        return disabled

    def handle_scan(self):
        """
        Try to find a job that can provide the base SRPM,
        download both SRPM and base SRPM and trigger the scan in OpenScanHub.
        """
        if not (base_build_job := self.find_base_build_job()):
            logger.debug("No base build job needed for diff scan found in the config.")
            return

        if self.build.scan:
            # see comment https://github.com/packit/packit-service/issues/2604#issuecomment-2444321483
            logger.debug(
                f"Scan for build {self.build.id} already submitted, "
                "scan task_id {self.build.scan.task_id}."
            )
            return

        if not (base_srpm_model := self.get_base_srpm_model(base_build_job)):
            logger.debug("Successful base SRPM build has not been found.")
            return

        logger.info("Preparing to trigger scan in OpenScanHub...")
        srpm_model = self.build.get_srpm_build()

        with tempfile.TemporaryDirectory() as directory:
            if not (paths := self.download_srpms(directory, base_srpm_model, srpm_model)):
                self.report(
                    state=BaseCommitStatus.neutral,
                    description=(
                        "It was not possible to download the SRPMs needed"
                        " for the differential scan."
                    ),
                    url=None,
                )
                return

            build_dashboard_url = get_copr_build_info_url(self.build.id)

            output = self.copr_build_helper.api.run_osh_build(
                srpm_path=paths[1],
                base_srpm=paths[0],
                comment=f"Submitted via Packit Service for {build_dashboard_url}",
            )

            if not output:
                self.report(
                    state=BaseCommitStatus.neutral,
                    description="Scan in OpenScanHub was not submitted successfully.",
                    url=None,
                )
                return

            logger.info("Scan submitted successfully.")

            response_dict = self.parse_dict_from_output(output)

            logger.debug(f"Parsed dict from output: {response_dict} ")

            scan = None
            if id := response_dict.get("id"):
                scan = self.build.add_scan(task_id=id)
            else:
                logger.debug(
                    "It was not possible to get the Open Scan Hub task_id from the response.",
                )

            if not (url := response_dict.get("url")):
                msg = "It was not possible to get the task URL from the OSH response."
                logger.debug(msg)
                self.report(
                    state=BaseCommitStatus.neutral,
                    description=msg,
                    url=None,
                )
                return
            if url and scan:
                scan.set_url(url)

            self.report(
                state=BaseCommitStatus.running,
                description=(
                    "Scan in OpenScanHub submitted successfully. Check the URL for more details."
                ),
                url=url,
            )

    def report(
        self,
        state: BaseCommitStatus,
        description: str,
        url: str,
        links_to_external_services: Optional[dict[str, str]] = None,
    ):
        self.copr_build_helper._report(
            state=state,
            description=description,
            url=url,
            check_names=["osh-diff-scan:fedora-rawhide-x86_64"],
            markdown_content=OPEN_SCAN_HUB_FEATURE_DESCRIPTION,
            links_to_external_services=links_to_external_services,
        )

    @staticmethod
    def parse_dict_from_output(output: str) -> dict:
        json_pattern = r"\{.*?\}"
        matches = re.findall(json_pattern, output, re.DOTALL)

        if not matches:
            return {}

        json_str = matches[-1]
        return json.loads(json_str)

    def find_base_build_job(self) -> Optional[JobConfig]:
        """
        Find the job in the config that can provide the base build for the scan
        (with `commit` trigger and same branch configured as the target PR branch).
        """
        base_build_job = None

        for job in self.copr_build_helper.package_config.get_job_views():
            if (
                job.type in (JobType.copr_build, JobType.build)
                and job.trigger == JobConfigTriggerType.commit
                and (
                    (
                        job.branch
                        and job.branch == self.copr_build_helper.pull_request_object.target_branch
                    )
                    or (
                        not job.branch
                        and self.copr_build_helper.project.default_branch
                        == self.copr_build_helper.pull_request_object.target_branch
                    )
                )
            ):
                base_build_job = job
                break

        return base_build_job

    def get_base_srpm_model(
        self,
        base_build_job: JobConfig,
    ) -> Optional[SRPMBuildModel]:
        """
        Get the SRPM build model of the latest successful Copr build
        for the given job config.
        """
        base_build_project_name = self.copr_build_helper.job_project_for_commit_job_config(
            base_build_job,
        )
        base_build_owner = self.copr_build_helper.job_owner_for_job_config(
            base_build_job,
        )

        def get_srpm_build(commit_sha):
            logger.debug(
                f"Searching for base build for {target_branch_commit} commit "
                f"in {base_build_owner}/{base_build_project_name} Copr project in our DB. ",
            )

            builds = CoprBuildTargetModel.get_all_by(
                commit_sha=commit_sha,
                project_name=base_build_project_name,
                owner=base_build_owner,
                target="fedora-rawhide-x86_64",
                status=BuildStatus.success,
            )
            try:
                return next(iter(builds)).get_srpm_build()
            except StopIteration:
                return None

        target_branch_commit = self.copr_build_helper.pull_request_object.target_branch_head_commit

        if srpm_build := get_srpm_build(target_branch_commit):
            return srpm_build

        for target_branch_commit in self.copr_build_helper.project.get_commits(
            self.copr_build_helper.pull_request_object.target_branch,
        )[1:]:
            if srpm_build := get_srpm_build(target_branch_commit):
                return srpm_build
        else:
            logger.debug("No matching base build found in our DB.")
            return None

    @staticmethod
    def download_srpms(
        directory: str,
        base_srpm_model: SRPMBuildModel,
        srpm_model: SRPMBuildModel,
    ) -> Optional[tuple[Path, Path]]:
        def download_srpm(srpm_model: SRPMBuildModel) -> Optional[Path]:
            if not srpm_model.url:
                logger.info(
                    f"SRPMBuildModel with copr_build_id={srpm_model.copr_build_id} "
                    "has status={srpm_model.status} "
                    "and empty url. Skipping download."
                )
                return None
            srpm_path = Path(directory).joinpath(basename(srpm_model.url))
            if not download_file(srpm_model.url, srpm_path):
                logger.info(f"Downloading of SRPM {srpm_model.url} was not successful.")
                return None
            return srpm_path

        if (base_srpm_path := download_srpm(base_srpm_model)) is None:
            return None

        if (srpm_path := download_srpm(srpm_model)) is None:
            return None

        return base_srpm_path, srpm_path

    @staticmethod
    def get_sarif_to_upload(url: str) -> Optional[str]:
        """
        Fetch the file content, convert to SARIF using csgrep,
        compress and encode it so that it can be uploaded to GitHub.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).joinpath(basename(url))
            if not download_file(url, path):
                logger.info(f"Downloading of file {url} was not successful.")
                return None

            # run `csgrep` to convert to SARIF format
            result = run_command(["csgrep", "--mode=sarif", str(path)], fail=False, output=True)

        if not result.success:
            logger.info(f"Conversion to SARIF was not successful: {result.stderr}")
            return None

        logger.info("Conversion to SARIF was successful, about to compress and encode.")

        try:
            # TODO replace csmock with OpenScanHub/ [Packit] OpenScanHub where needed
            #  so that this name is displayed later in GitHub UI
            sarif_data = result.stdout.encode("utf-8")
            compressed_data = gzip.compress(sarif_data)
            base64_encoded_data = base64.b64encode(compressed_data).decode("utf-8")

            logger.info("SARIF file successfully compressed and encoded.")
            return base64_encoded_data

        except Exception as e:
            logger.error(f"An error occurred during compression and encoding: {e}")
            return None

    def upload_sarif(self, data: str):
        """
        Upload the encoded SARIF to GitHub.
        """
        if self.copr_build_helper.job_build.merge_pr_in_ci:
            # TODO this is not really correct and we need to discuss it
            commit_sha = self.copr_build_helper.pull_request_object.merge_commit_sha
            ref = f"refs/pull/{self.copr_build_helper.pr_id}/merge"
        else:
            commit_sha = self.copr_build_helper.db_project_event.commit_sha
            ref = f"refs/pull/{self.copr_build_helper.pr_id}/head"

        # there is no PyGithub support yet, API docs:
        # https://docs.github.com/en/rest/code-scanning/code-scanning?
        # apiVersion=2022-11-28#upload-an-analysis-as-sarif-data--parameters
        payload = {
            "commit_sha": commit_sha,
            "ref": ref,
            "sarif": data,
        }
        if not isinstance(self.copr_build_helper.project, GithubProject):
            return

        pygithub_repo = self.copr_build_helper.project.github_repo
        pygithub_repo._requester.requestJsonAndCheck(
            "POST",
            f"{pygithub_repo.url}/code-scanning/sarifs",
            input=payload,
        )
