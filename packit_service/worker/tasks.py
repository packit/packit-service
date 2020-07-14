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
from packit_service.service.events import (
    CoprBuildEvent,
    InstallationEvent,
    KojiBuildEvent,
    PullRequestLabelAction,
    TestingFarmResult,
    EventData,
    TestResult,
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
    GithubTestingFarmHandler,
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

from packit_service.worker.handlers.abstract import TaskName
from packit_service.utils import load_package_config, load_job_config

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
    return SteveJobs().process_message(event=event, topic=topic, source=source)


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
@celery_app.task(name=TaskName.copr_build_start)
def run_copr_build_start_handler(event: dict, package_config: dict, job_config: dict):
    handler = CoprBuildStartHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        data=EventData.from_event_dict(event),
        copr_event=CoprBuildEvent.from_event_dict(event),
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.copr_build_end)
def run_copr_build_end_handler(event: dict, package_config: dict, job_config: dict):
    handler = CoprBuildEndHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        data=EventData.from_event_dict(event),
        copr_event=CoprBuildEvent.from_event_dict(event),
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.release_copr_build)
def run_release_copr_build_handler(event: dict, package_config: dict, job_config: dict):
    handler = ReleaseCoprBuildHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        data=EventData.from_event_dict(event),
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.pr_copr_build)
def run_pr_copr_build_handler(event: dict, package_config: dict, job_config: dict):
    handler = PullRequestCoprBuildHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        data=EventData.from_event_dict(event),
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.pr_comment_copr_build)
def run_pr_comment_copr_build_handler(
    event: dict, package_config: dict, job_config: dict
):
    handler = GitHubPullRequestCommentCoprBuildHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        data=EventData.from_event_dict(event),
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.push_copr_build)
def run_push_copr_build_handler(event: dict, package_config: dict, job_config: dict):
    handler = PushCoprBuildHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        data=EventData.from_event_dict(event),
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.installation)
def run_installation_handler(event: dict, package_config: dict, job_config: dict):
    handler = GithubAppInstallationHandler(
        package_config=None,
        job_config=None,
        data=None,
        installation_event=InstallationEvent.from_event_dict(event),
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.testing_farm)
def run_testing_farm_handler(
    event: dict, package_config: dict, job_config: dict, chroot: str, build_id: int
):
    handler = GithubTestingFarmHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        data=EventData.from_event_dict(event),
        chroot=chroot,
        build_id=build_id,
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.testing_farm_comment)
def run_testing_farm_comment_handler(
    event: dict, package_config: dict, job_config: dict
):
    handler = GitHubPullRequestCommentTestingFarmHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        data=EventData.from_event_dict(event),
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.testing_farm_results)
def run_testing_farm_results_handler(
    event: dict, package_config: dict, job_config: dict
):
    handler = TestingFarmResultsHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        data=EventData.from_event_dict(event),
        tests=[TestResult(**test) for test in event.get("tests", [])],
        result=TestingFarmResult(event.get("result")) if event.get("result") else None,
        pipeline_id=event.get("pipeline_id"),
        log_url=event.get("log_url"),
        copr_chroot=event.get("copr_chroot"),
        message=event.get("message"),
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.propose_update_comment)
def run_propose_update_comment_handler(
    event: dict, package_config: dict, job_config: dict
):
    handler = GitHubIssueCommentProposeUpdateHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        data=EventData.from_event_dict(event),
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.propose_downstream)
def run_propose_downstream_handler(event: dict, package_config: dict, job_config: dict):
    handler = ProposeDownstreamHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        data=EventData.from_event_dict(event),
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.release_koji_build)
def run_release_koji_build_handler(event: dict, package_config: dict, job_config: dict):
    handler = ReleaseGithubKojiBuildHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        data=EventData.from_event_dict(event),
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.pr_koji_build)
def run_pr_koji_build_handler(event: dict, package_config: dict, job_config: dict):
    handler = PullRequestGithubKojiBuildHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        data=EventData.from_event_dict(event),
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.push_koji_build)
def run_push_koji_build_handler(event: dict, package_config: dict, job_config: dict):
    handler = PushGithubKojiBuildHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        data=EventData.from_event_dict(event),
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.distgit_commit)
def run_distgit_commit_handler(event: dict, package_config: dict, job_config: dict):
    handler = NewDistGitCommitHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        data=EventData.from_event_dict(event),
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.pagure_pr_comment_copr_build)
def run_pagure_pr_comment_copr_build_handler(
    event: dict, package_config: dict, job_config: dict
):
    handler = PagurePullRequestCommentCoprBuildHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        data=EventData.from_event_dict(event),
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.pagure_pr_label)
def run_pagure_pr_label_handler(event: dict, package_config: dict, job_config: dict):
    handler = PagurePullRequestLabelHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        data=EventData.from_event_dict(event),
        labels=event.get("labels"),
        action=PullRequestLabelAction(event.get("action")),
        base_repo_owner=event.get("base_repo_owner"),
        base_repo_name=event.get("base_repo_name"),
        base_repo_namespace=event.get("base_repo_namespace"),
    )
    return get_handlers_task_results(handler.run_job(), event)


@celery_app.task(name=TaskName.koji_build_report)
def run_koji_build_report_handler(event: dict, package_config: dict, job_config: dict):
    handler = KojiBuildReportHandler(
        package_config=load_package_config(package_config),
        job_config=load_job_config(job_config),
        data=EventData.from_event_dict(event),
        koji_event=KojiBuildEvent.from_event_dict(event),
    )
    return get_handlers_task_results(handler.run_job(), event)


def get_handlers_task_results(results: dict, event: dict) -> dict:
    # include original event to provide more info
    return {"job": results, "event": event}
