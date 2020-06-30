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
import logging
from typing import Optional

from packit_service.celerizer import celery_app
from packit_service.models import TaskResultModel
from packit_service.service.events import (
    CoprBuildEvent,
    InstallationEvent,
    KojiBuildEvent,
    PullRequestLabelAction,
    TestingFarmResult,
    EventData,
)
from packit_service.worker.build.babysit import check_copr_build
from packit_service.worker.jobs import SteveJobs
from packit_service.worker.handlers.github_handlers import (
    GithubAppInstallationHandler,
    GitHubIssueCommentProposeUpdateHandler,
    ReleaseGithubKojiBuildHandler,
    PushGithubKojiBuildHandler,
    PullRequestGithubKojiBuildHandler,
    PushCoprBuildHandler,
    ReleaseCoprBuildHandler,
    PullRequestCoprBuildHandler,
    ProposeDownstreamHandler,
    GitHubPullRequestCommentTestingFarmHandler,
    GitHubPullRequestCommentCoprBuildHandler,
)

from packit_service.worker.handlers.fedmsg_handlers import (
    CoprBuildStartHandler,
    CoprBuildEndHandler,
    KojiBuildReportHandler,
    NewDistGitCommitHandler,
)

from packit_service.worker.handlers.pagure_handlers import (
    PagurePullRequestCommentCoprBuildHandler,
    PagurePullRequestLabelHandler,
)

from packit_service.worker.handlers.testing_farm_handlers import (
    TestingFarmResultsHandler,
)

from packit.schema import PackageConfigSchema, JobConfigSchema

logger = logging.getLogger(__name__)

# debug logs of these are super-duper verbose
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("github").setLevel(logging.WARNING)
logging.getLogger("kubernetes").setLevel(logging.WARNING)
# info is just enough
logging.getLogger("ogr").setLevel(logging.INFO)
# easier debugging
logging.getLogger("packit").setLevel(logging.DEBUG)
logging.getLogger("sandcastle").setLevel(logging.DEBUG)


@celery_app.task(name="task.steve_jobs.process_message", bind=True)
def process_message(
    self, event: dict, topic: str = None, source: str = None
) -> Optional[dict]:
    """
    Base celery task for processing messages.

    :param event: event data
    :param topic: event topic
    :param source: event source
    :return: dictionary containing task results
    """
    task_results: dict = SteveJobs().process_message(
        event=event, topic=topic, source=source
    )
    if task_results:
        TaskResultModel.add_task_result(
            task_id=self.request.id, task_result_dict=task_results
        )
    return task_results


@celery_app.task(
    bind=True,
    name="task.babysit_copr_build",
    retry_backoff=60,  # retry again in 60s, 120s, 240s, 480s...
    retry_backoff_max=60 * 60 * 8,  # is 8 hours okay? gcc/kernel build really long
    max_retries=7,
)
def babysit_copr_build(self, build_id: int):
    """ check status of a copr build and update it in DB """
    if not check_copr_build(build_id=build_id):
        self.retry()


# tasks for running the handlers
@celery_app.task(name="task.run_copr_build_start_handler")
def run_copr_build_start_handler(event: dict, package_config: dict, job_config: dict):
    handler = CoprBuildStartHandler(
        package_config=PackageConfigSchema().load_config(package_config),
        job_config=JobConfigSchema().load_config(job_config),
        data=EventData.from_event_dict(event),
        copr_event=CoprBuildEvent.from_event_dict(event),
    )
    return handler.run_job()


@celery_app.task(name="task.run_copr_build_end_handler")
def run_copr_build_end_handler(event: dict, package_config: dict, job_config: dict):
    handler = CoprBuildEndHandler(
        package_config=PackageConfigSchema().load_config(package_config),
        job_config=JobConfigSchema().load_config(job_config),
        data=EventData.from_event_dict(event),
        copr_event=CoprBuildEvent.from_event_dict(event),
    )
    return handler.run_job()


@celery_app.task(name="task.run_release_copr_build_handler")
def run_release_copr_build_handler(event: dict, package_config: dict, job_config: dict):
    handler = ReleaseCoprBuildHandler(
        package_config=PackageConfigSchema().load_config(package_config),
        job_config=JobConfigSchema().load_config(job_config),
        data=EventData.from_event_dict(event),
    )
    return handler.run_job()


@celery_app.task(name="task.run_pr_copr_build_handler")
def run_pr_copr_build_handler(event: dict, package_config: dict, job_config: dict):
    handler = PullRequestCoprBuildHandler(
        package_config=PackageConfigSchema().load_config(package_config),
        job_config=JobConfigSchema().load_config(job_config),
        data=EventData.from_event_dict(event),
    )
    return handler.run_job()


@celery_app.task(name="task.run_pr_comment_copr_build_handler")
def run_pr_comment_copr_build_handler(
    event: dict, package_config: dict, job_config: dict
):
    handler = GitHubPullRequestCommentCoprBuildHandler(
        package_config=PackageConfigSchema().load_config(package_config),
        job_config=JobConfigSchema().load_config(job_config),
        data=EventData.from_event_dict(event),
    )
    return handler.run_job()


@celery_app.task(name="task.run_push_copr_build_handler")
def run_push_copr_build_handler(event: dict, package_config: dict, job_config: dict):
    handler = PushCoprBuildHandler(
        package_config=PackageConfigSchema().load_config(package_config),
        job_config=JobConfigSchema().load_config(job_config),
        data=EventData.from_event_dict(event),
    )
    return handler.run_job()


@celery_app.task(name="task.run_installation_handler")
def run_installation_handler(event: dict, package_config: dict, job_config: dict):
    handler = GithubAppInstallationHandler(
        package_config=PackageConfigSchema().load_config(package_config),
        job_config=JobConfigSchema().load_config(job_config),
        data=None,
        installation_event=InstallationEvent.from_event_dict(event),
    )
    return handler.run_job()


@celery_app.task(name="task.run_testing_farm_comment_handler")
def run_testing_farm_comment_handler(
    event: dict, package_config: dict, job_config: dict
):
    handler = GitHubPullRequestCommentTestingFarmHandler(
        package_config=PackageConfigSchema().load_config(package_config),
        job_config=JobConfigSchema().load_config(job_config),
        data=EventData.from_event_dict(event),
    )
    return handler.run_job()


@celery_app.task(name="task.run_testing_farm_results_handler")
def run_testing_farm_results_handler(
    event: dict, package_config: dict, job_config: dict
):
    tests = event.get("tests")
    result = TestingFarmResult(event.get("result")) if event.get("result") else None
    pipeline_id = event.get("pipeline_id")
    log_url = event.get("log_url")
    copr_chroot = event.get("copr_chroot")
    message = event.get("message")

    handler = TestingFarmResultsHandler(
        package_config=PackageConfigSchema().load_config(package_config),
        job_config=JobConfigSchema().load_config(job_config),
        data=EventData.from_event_dict(event),
        tests=tests,
        result=result,
        pipeline_id=pipeline_id,
        log_url=log_url,
        copr_chroot=copr_chroot,
        message=message,
    )
    return handler.run_job()


@celery_app.task(name="task.run_propose_update_comment_handler")
def run_propose_update_comment_handler(
    event: dict, package_config: dict, job_config: dict
):
    handler = GitHubIssueCommentProposeUpdateHandler(
        package_config=PackageConfigSchema().load_config(package_config),
        job_config=JobConfigSchema().load_config(job_config),
        data=EventData.from_event_dict(event),
    )
    return handler.run_job()


@celery_app.task(name="task.run_propose_downstream_handler")
def run_propose_downstream_handler(event: dict, package_config: dict, job_config: dict):
    handler = ProposeDownstreamHandler(
        package_config=PackageConfigSchema().load_config(package_config),
        job_config=JobConfigSchema().load_config(job_config),
        data=EventData.from_event_dict(event),
    )
    return handler.run_job()


@celery_app.task(name="task.run_release_koji_build_handler")
def run_release_koji_build_handler(event: dict, package_config: dict, job_config: dict):
    handler = ReleaseGithubKojiBuildHandler(
        package_config=PackageConfigSchema().load_config(package_config),
        job_config=JobConfigSchema().load_config(job_config),
        data=EventData.from_event_dict(event),
    )
    return handler.run_job()


@celery_app.task(name="task.run_pr_koji_build_handler")
def run_pr_koji_build_handler(event: dict, package_config: dict, job_config: dict):
    handler = PullRequestGithubKojiBuildHandler(
        package_config=PackageConfigSchema().load_config(package_config),
        job_config=JobConfigSchema().load_config(job_config),
        data=EventData.from_event_dict(event),
    )
    return handler.run_job()


@celery_app.task(name="task.run_push_koji_build_handler")
def run_push_koji_build_handler(event: dict, package_config: dict, job_config: dict):
    handler = PushGithubKojiBuildHandler(
        package_config=PackageConfigSchema().load_config(package_config),
        job_config=JobConfigSchema().load_config(job_config),
        data=EventData.from_event_dict(event),
    )
    return handler.run_job()


@celery_app.task(name="task.run_distgit_commit_handler")
def run_distgit_commit_handler(event: dict, package_config: dict, job_config: dict):
    handler = NewDistGitCommitHandler(
        package_config=PackageConfigSchema().load_config(package_config),
        job_config=JobConfigSchema().load_config(job_config),
        data=EventData.from_event_dict(event),
    )
    return handler.run_job()


@celery_app.task(name="task.run_pagure_pr_comment_copr_build_handler")
def run_pagure_pr_comment_copr_build_handler(
    event: dict, package_config: dict, job_config: dict
):
    handler = PagurePullRequestCommentCoprBuildHandler(
        package_config=PackageConfigSchema().load_config(package_config),
        job_config=JobConfigSchema().load_config(job_config),
        data=EventData.from_event_dict(event),
    )
    return handler.run_job()


@celery_app.task(name="task.run_pagure_pr_label_handler")
def run_pagure_pr_label_handler(event: dict, package_config: dict, job_config: dict):
    labels = event.get("labels")
    action = PullRequestLabelAction(event.get("action"))
    base_repo_owner = event.get("base_repo_owner")
    base_repo_namespace = event.get("base_repo_namespace")
    base_repo_name = event.get("base_repo_name")

    handler = PagurePullRequestLabelHandler(
        package_config=PackageConfigSchema().load_config(package_config),
        job_config=JobConfigSchema().load_config(job_config),
        data=EventData.from_event_dict(event),
        labels=labels,
        action=action,
        base_repo_owner=base_repo_owner,
        base_repo_name=base_repo_name,
        base_repo_namespace=base_repo_namespace,
    )
    return handler.run_job()


@celery_app.task(name="task.run_koji_build_report_handler")
def run_koji_build_report_handler(event: dict, package_config: dict, job_config: dict):
    handler = KojiBuildReportHandler(
        package_config=PackageConfigSchema().load_config(package_config),
        job_config=JobConfigSchema().load_config(job_config),
        data=EventData.from_event_dict(event),
        koji_event=KojiBuildEvent.from_event_dict(event),
    )
    return handler.run_job()
