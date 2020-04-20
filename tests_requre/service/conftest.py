import pytest

from packit_service.models import (
    CoprBuildModel,
    get_sa_session,
    SRPMBuildModel,
    PullRequestModel,
    GitProjectModel,
    WhitelistModel,
    GitBranchModel,
    InstallationModel,
)


def clean_db():
    with get_sa_session() as session:
        session.query(CoprBuildModel).delete()
        session.query(SRPMBuildModel).delete()
        session.query(WhitelistModel).delete()
        session.query(InstallationModel).delete()
        session.query(GitBranchModel).delete()
        session.query(PullRequestModel).delete()
        session.query(GitProjectModel).delete()


@pytest.fixture()
def clean_before_and_after():
    clean_db()
    yield
    clean_db()


# Fill up some data in the db and then use the same data to check if API is working as expected.
# We don't pass some of this data into the db. Instead the API/DB functions should create it.
# Ex: https_url or the chroot lists.
build_info_dict = {
    "project": "Shield-tahiti-7",
    "owner": "shield",
    "build_id": "270520",
    "status": "success",
    "status_per_chroot": {"fedora-43-x86_64": "success", "fedora-42-x86_64": "pending"},
    "chroots": ["fedora-43-x86_64", "fedora-42-x86_64"],
    "commit_sha": "80201a74d96c",
    "web_url": "https://copr.something.somewhere/270520",
    "srpm_logs": "Some boring logs.",
    "ref": "80201a74d96c",
    "repo_namespace": "marvel",
    "repo_name": "aos",
    "git_repo": "https://github.com/marvel/aos",
    "https_url": "https://github.com/marvel/aos.git",
    "pr_id": 2705,
}


@pytest.fixture()
def pr_model():
    yield PullRequestModel.get_or_create(
        pr_id=build_info_dict["pr_id"],
        namespace=build_info_dict["repo_namespace"],
        repo_name=build_info_dict["repo_name"],
    )


@pytest.fixture()
def different_pr_model():
    yield PullRequestModel.get_or_create(
        pr_id=4, namespace="the-namespace", repo_name="the-repo-name"
    )


@pytest.fixture()
def multiple_copr_builds(pr_model, different_pr_model):
    srpm_build = SRPMBuildModel.create(build_info_dict["srpm_logs"])
    yield [
        CoprBuildModel.get_or_create(
            build_id=build_info_dict["build_id"],
            commit_sha=build_info_dict["ref"],
            project_name=build_info_dict["project"],
            owner=build_info_dict["owner"],
            web_url=build_info_dict["web_url"],
            target="fedora-43-x86_64",
            status="success",
            srpm_build=srpm_build,
            trigger_model=pr_model,
        ),
        CoprBuildModel.get_or_create(
            build_id=build_info_dict["build_id"],
            commit_sha=build_info_dict["ref"],
            project_name=build_info_dict["project"],
            owner=build_info_dict["owner"],
            web_url=build_info_dict["web_url"],
            target="fedora-42-x86_64",
            status="pending",
            srpm_build=srpm_build,
            trigger_model=pr_model,
        ),
        CoprBuildModel.get_or_create(
            build_id="123456",
            commit_sha="687abc76d67d",
            project_name="SomeUser-hello-world-9",
            owner="packit",
            web_url="https://copr.something.somewhere/123456",
            target="fedora-43-x86_64",
            status="success",
            srpm_build=srpm_build,
            trigger_model=different_pr_model,
        ),
    ]


# Create new whitelist entry
@pytest.fixture()
def new_whitelist_entry():
    with get_sa_session() as session:
        session.query(WhitelistModel).delete()
        yield WhitelistModel.add_account(
            account_name="Rayquaza", status="approved_manually"
        )
