import json

from flexmock import flexmock
from ogr.abstract import GitProject, GitService
from packit.api import PackitAPI
from packit.config import PackageConfig, JobConfig, JobType, JobTriggerType
from packit.exceptions import FailedCreateSRPM

from packit_service.config import ServiceConfig
from packit_service.models import CoprBuild, SRPMBuild
from packit_service.service.models import CoprBuild as RedisCoprBuild
from packit_service.worker import sentry_integration
from packit_service.worker.build.copr_build import CoprBuildJobHelper
from packit_service.worker.build.build_helper import BuildStatusReporter
from packit_service.worker.parser import Parser
from tests.spellbook import DATA_DIR


def pull_request():
    with open(DATA_DIR / "webhooks" / "github_pr_event.json", "r") as outfile:
        return json.load(outfile)


class FakeCoprBuildModel:
    build_id = 0

    def save(self):
        pass

    def add_build(self):
        pass


def build_handler(metadata=None, trigger=None, jobs=None):
    if not metadata:
        metadata = {
            "owner": "nobody",
            "targets": [
                "fedora-29-x86_64",
                "fedora-30-x86_64",
                "fedora-31-x86_64",
                "fedora-rawhide-x86_64",
            ],
        }
    jobs = jobs or []
    jobs.append(
        JobConfig(
            job=JobType.copr_build,
            trigger=trigger or JobTriggerType.pull_request,
            metadata=metadata,
        )
    )
    pkg_conf = PackageConfig(jobs=jobs, downstream_package_name="dummy")
    event = Parser.parse_pr_event(pull_request())
    handler = CoprBuildJobHelper(
        config=ServiceConfig(),
        package_config=pkg_conf,
        project=GitProject("", GitService(), ""),
        event=event,
    )
    handler._api = PackitAPI(ServiceConfig(), pkg_conf)
    return handler


def test_copr_build_check_names():
    metadata = {"owner": "nobody", "targets": ["bright-future-x86_64"]}
    handler = build_handler(metadata)
    flexmock(BuildStatusReporter).should_receive("set_status").with_args(
        state="pending",
        description="Building SRPM ...",
        check_name="packit-stg/rpm-build-bright-future-x86_64",
        url="",
    ).and_return()
    flexmock(BuildStatusReporter).should_receive("set_status").with_args(
        state="pending",
        description="Building RPM ...",
        check_name="packit-stg/rpm-build-bright-future-x86_64",
        url="https://localhost:5000/copr-build/1/logs",
    ).and_return()

    flexmock(GitProject).should_receive("set_commit_status").and_return().never()
    flexmock(RedisCoprBuild).should_receive("create").and_return(FakeCoprBuildModel())
    flexmock(SRPMBuild).should_receive("create").and_return(SRPMBuild())
    flexmock(CoprBuild).should_receive("get_or_create").and_return(CoprBuild(id=1))
    flexmock(PackitAPI).should_receive("run_copr_build").and_return(1, None)
    assert handler.run_copr_build()["success"]


def test_copr_build_success_set_test_check():
    # status is set for each build-target (4x):
    #  - Building SRPM ...
    #  - Building RPM ...
    # status is set for each test-target (4x):
    #  - Building SRPM ...
    #  - Building RPM ...
    test_job = JobConfig(
        job=JobType.tests, trigger=JobTriggerType.pull_request, metadata={}
    )
    handler = build_handler(jobs=[test_job])
    flexmock(GitProject).should_receive("set_commit_status").and_return().times(16)
    flexmock(RedisCoprBuild).should_receive("create").and_return(FakeCoprBuildModel())
    flexmock(SRPMBuild).should_receive("create").and_return(SRPMBuild())
    flexmock(CoprBuild).should_receive("get_or_create").and_return(CoprBuild(id=1))
    flexmock(PackitAPI).should_receive("run_copr_build").and_return(1, None).once()
    assert handler.run_copr_build()["success"]


def test_copr_build_success():
    # status is set for each build-target (4x):
    #  - Building SRPM ...
    #  - Building RPM ...
    handler = build_handler()
    flexmock(GitProject).should_receive("set_commit_status").and_return().times(8)
    flexmock(RedisCoprBuild).should_receive("create").and_return(FakeCoprBuildModel())
    flexmock(SRPMBuild).should_receive("create").and_return(SRPMBuild())
    flexmock(CoprBuild).should_receive("get_or_create").and_return(CoprBuild(id=1))
    flexmock(PackitAPI).should_receive("run_copr_build").and_return(1, None).once()
    assert handler.run_copr_build()["success"]


def test_copr_build_fails_in_packit():
    # status is set for each build-target (4x):
    #  - Building SRPM ...
    #  - Build failed, check latest comment for details.
    handler = build_handler()
    flexmock(GitProject, pr_comment=lambda *args, **kw: None)
    flexmock(GitProject).should_receive("set_commit_status").and_return().times(8)
    flexmock(GitProject).should_receive("pr_comment").and_return().once()
    flexmock(RedisCoprBuild).should_receive("create").and_return(FakeCoprBuildModel())
    flexmock(SRPMBuild).should_receive("create").and_return(SRPMBuild())
    flexmock(CoprBuild).should_receive("get_or_create").and_return(CoprBuild(id=1))
    flexmock(sentry_integration).should_receive("send_to_sentry").and_return().once()
    flexmock(PackitAPI).should_receive("run_copr_build").and_raise(
        FailedCreateSRPM, "some error"
    )
    assert not handler.run_copr_build()["success"]


def test_copr_build_no_targets():
    # status is set for each build-target (fedora-stable => 2x):
    #  - Building SRPM ...
    #  - Building RPM ...
    handler = build_handler(metadata={"owner": "nobody"})
    flexmock(GitProject).should_receive("set_commit_status").and_return().times(4)
    flexmock(RedisCoprBuild).should_receive("create").and_return(FakeCoprBuildModel())
    flexmock(SRPMBuild).should_receive("create").and_return(SRPMBuild())
    flexmock(CoprBuild).should_receive("get_or_create").and_return(CoprBuild(id=1))
    flexmock(PackitAPI).should_receive("run_copr_build").and_return(1, None).once()
    assert handler.run_copr_build()["success"]
