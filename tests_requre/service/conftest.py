"""
These tests require a psql database with a schema:
```
export POSTGRESQL_USER=packit
export POSTGRESQL_PASSWORD=secret-password
export POSTGRESQL_DATABASE=packit
export POSTGRESQL_SERVICE_HOST=0.0.0.0
$ docker-compose -d postgres
$ alembic upgrade head
```
"""

import pytest

from packit_service.models import (
    CoprBuildModel,
    get_sa_session,
    SRPMBuildModel,
    PullRequestModel,
    GitProjectModel,
    WhitelistModel,
    GitBranchModel,
    ProjectReleaseModel,
    IssueModel,
    JobTriggerModel,
    JobTriggerModelType,
    KojiBuildModel,
    TFTTestRunModel,
    TestingFarmResult,
    TaskResultModel,
    InstallationModel,
)
    

@pytest.fixture()
def clean_db():
    with get_sa_session() as session:
        session.query(CoprBuildModel).delete()
        session.query(SRPMBuildModel).delete()
        session.query(WhitelistModel).delete()
        session.query(InstallationModel).delete()
        session.query(GitBranchModel).delete()
        session.query(PullRequestModel).delete()
        session.query(GitProjectModel).delete()

def a_copr_build_for_pr(pr_model):
    srpm_build = SRPMBuildModel.create("asd\nqwe\n")
    yield CoprBuildModel.get_or_create(
        build_id="123456",
        commit_sha="687abc76d67d",
        project_name="SomeUser-hello-world-9",
        owner="packit",
        web_url="https://copr.something.somewhere/123456",
        target=TARGET,
        status="pending",
        srpm_build=srpm_build,
        trigger_model=pr_model,
    )