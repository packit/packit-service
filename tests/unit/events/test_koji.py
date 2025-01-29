# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from flexmock import flexmock
from ogr.services.github import GithubProject
from ogr.services.pagure import PagureProject
from packit.utils.koji_helper import KojiHelper

from packit_service.constants import KojiBuildState, KojiTaskState
from packit_service.events import koji as events
from packit_service.models import KojiBuildTargetModel
from packit_service.worker.parser import Parser


def test_parse_koji_build_scratch_event_start(koji_build_scratch_start, koji_build_pr):
    flexmock(KojiBuildTargetModel).should_receive("get_by_task_id").and_return(
        koji_build_pr,
    )

    event_object = Parser.parse_event(koji_build_scratch_start)

    assert isinstance(event_object, events.result.Task)
    assert event_object.task_id == 45270170
    assert event_object.state == KojiTaskState.open
    assert not event_object.rpm_build_task_ids

    assert isinstance(event_object.project, GithubProject)
    assert event_object.project.full_repo_name == "foo/bar"


def test_parse_koji_build_scratch_event_end(koji_build_scratch_end, koji_build_pr):
    flexmock(KojiBuildTargetModel).should_receive("get_by_task_id").and_return(
        koji_build_pr,
    )

    event_object = Parser.parse_event(koji_build_scratch_end)

    assert isinstance(event_object, events.result.Task)
    assert event_object.task_id == 45270170
    assert event_object.state == KojiTaskState.closed
    assert event_object.rpm_build_task_ids == {"noarch": 45270227}
    assert event_object.get_koji_build_rpm_tasks_logs_urls() == {
        "noarch": "https://kojipkgs.fedoraproject.org//work/tasks/227/45270227/build.log",
    }

    flexmock(GithubProject).should_receive("get_pr").with_args(pr_id=123).and_return(
        flexmock(author="the-fork"),
    )
    assert isinstance(event_object.project, GithubProject)
    assert event_object.project.full_repo_name == "foo/bar"


def test_parse_koji_build_event_start_old_format(
    koji_build_start_old_format,
    mock_config,
):
    event_object = Parser.parse_event(koji_build_start_old_format)

    assert isinstance(event_object, events.result.Build)
    assert event_object.build_id == 1864700
    assert event_object.state == KojiBuildState.building
    assert not event_object.old_state
    assert event_object.task_id == 79721403
    assert event_object.package_name == "packit"
    assert event_object.commit_sha == "0eb3e12005cb18f15d3054020f7ac934c01eae08"
    assert event_object.branch_name == "rawhide"
    assert event_object.git_ref == "rawhide"
    assert event_object.epoch is None
    assert event_object.version == "0.43.0"
    assert event_object.release == "1.fc36"
    assert event_object.nvr == "packit-0.43.0-1.fc36"
    assert event_object.project_url == "https://src.fedoraproject.org/rpms/packit"

    assert isinstance(event_object.project, PagureProject)
    assert event_object.project.full_repo_name == "rpms/packit"

    packit_yaml = (
        "{'specfile_path': 'packit.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'sync_from_downstream'}],"
        "'downstream_package_name': 'packit'}"
    )
    flexmock(PagureProject).should_receive("get_file_content").with_args(
        path=".distro/source-git.yaml",
        ref="rawhide",
    ).and_raise(FileNotFoundError, "Not found.")
    flexmock(PagureProject).should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref="rawhide",
    ).and_return(packit_yaml)

    assert event_object.packages_config


def test_parse_koji_build_event_start_rawhide(koji_build_start_rawhide, mock_config):
    event_object = Parser.parse_event(koji_build_start_rawhide)

    assert isinstance(event_object, events.result.Build)
    assert event_object.build_id == 1874074
    assert event_object.state == KojiBuildState.building
    assert not event_object.old_state
    assert event_object.task_id == 80860894
    assert event_object.package_name == "python-ogr"
    assert event_object.commit_sha == "e029dd5250dde9a37a2cdddb6d822d973b09e5da"
    assert event_object.branch_name == "rawhide"
    assert event_object.git_ref == "rawhide"
    assert event_object.epoch is None
    assert event_object.version == "0.34.0"
    assert event_object.release == "1.fc36"
    assert event_object.nvr == "python-ogr-0.34.0-1.fc36"
    assert event_object.project_url == "https://src.fedoraproject.org/rpms/python-ogr"

    assert event_object.start_time is not None
    assert event_object.completion_time is None

    assert isinstance(event_object.project, PagureProject)
    assert event_object.project.full_repo_name == "rpms/python-ogr"

    packit_yaml = (
        "{'specfile_path': 'python-ogr.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'sync_from_downstream'}],"
        "'downstream_package_name': 'python-ogr'}"
    )
    flexmock(PagureProject).should_receive("get_file_content").with_args(
        path=".distro/source-git.yaml",
        ref="rawhide",
    ).and_raise(FileNotFoundError, "Not found.")
    flexmock(PagureProject).should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref="rawhide",
    ).and_return(packit_yaml)

    assert event_object.packages_config


def test_parse_koji_build_event_start_f36(koji_build_start_f36, mock_config):
    event_object = Parser.parse_event(koji_build_start_f36)

    assert isinstance(event_object, events.result.Build)
    assert event_object.build_id == 1874070
    assert event_object.state == KojiBuildState.building
    assert not event_object.old_state
    assert event_object.task_id == 80860789
    assert event_object.package_name == "python-ogr"
    assert event_object.commit_sha == "51b57ec04f5e6e9066ac859a1408cfbf1ead307e"
    assert event_object.branch_name == "f36"
    assert event_object.git_ref == "f36"
    assert event_object.epoch is None
    assert event_object.version == "0.34.0"
    assert event_object.release == "1.fc36"
    assert event_object.nvr == "python-ogr-0.34.0-1.fc36"
    assert event_object.project_url == "https://src.fedoraproject.org/rpms/python-ogr"

    assert event_object.start_time is not None
    assert event_object.completion_time is None

    assert isinstance(event_object.project, PagureProject)
    assert event_object.project.full_repo_name == "rpms/python-ogr"

    packit_yaml = (
        "{'specfile_path': 'python-ogr.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'sync_from_downstream'}],"
        "'downstream_package_name': 'python-ogr'}"
    )
    flexmock(PagureProject).should_receive("get_file_content").with_args(
        path=".distro/source-git.yaml",
        ref="rawhide",
    ).and_raise(FileNotFoundError, "Not found.")
    flexmock(PagureProject).should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref="rawhide",
    ).and_return(packit_yaml)

    assert event_object.packages_config


def test_parse_koji_build_event_start_epel8(koji_build_start_epel8, mock_config):
    event_object = Parser.parse_event(koji_build_start_epel8)

    assert isinstance(event_object, events.result.Build)
    assert event_object.build_id == 1874072
    assert event_object.state == KojiBuildState.building
    assert not event_object.old_state
    assert event_object.task_id == 80860791
    assert event_object.owner == "packit"
    assert event_object.package_name == "python-ogr"
    assert event_object.commit_sha == "23806a208e32cc937f3a6eb151c62cbbc10d8f96"
    assert event_object.branch_name == "epel8"
    assert event_object.git_ref == "epel8"
    assert event_object.epoch is None
    assert event_object.version == "0.34.0"
    assert event_object.release == "1.el8"
    assert event_object.project_url == "https://src.fedoraproject.org/rpms/python-ogr"

    assert event_object.start_time is not None
    assert event_object.completion_time is None

    assert isinstance(event_object.project, PagureProject)
    assert event_object.project.full_repo_name == "rpms/python-ogr"

    packit_yaml = (
        "{'specfile_path': 'python-ogr.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'sync_from_downstream'}],"
        "'downstream_package_name': 'python-ogr'}"
    )
    flexmock(PagureProject).should_receive("get_file_content").with_args(
        path=".distro/source-git.yaml",
        ref="rawhide",
    ).and_raise(FileNotFoundError, "Not found.")
    flexmock(PagureProject).should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref="rawhide",
    ).and_return(packit_yaml)

    assert event_object.packages_config


def test_parse_koji_build_event_completed_old_format(
    koji_build_completed_old_format,
    mock_config,
):
    event_object = Parser.parse_event(koji_build_completed_old_format)

    assert isinstance(event_object, events.result.Build)
    assert event_object.build_id == 1864700
    assert event_object.state == KojiBuildState.complete
    assert event_object.old_state == KojiBuildState.building
    assert event_object.task_id == 79721403
    assert event_object.package_name == "packit"
    assert event_object.commit_sha == "0eb3e12005cb18f15d3054020f7ac934c01eae08"
    assert event_object.branch_name == "rawhide"
    assert event_object.git_ref == "rawhide"
    assert event_object.epoch is None
    assert event_object.version == "0.43.0"
    assert event_object.release == "1.fc36"
    assert event_object.nvr == "packit-0.43.0-1.fc36"
    assert event_object.project_url == "https://src.fedoraproject.org/rpms/packit"

    assert isinstance(event_object.project, PagureProject)
    assert event_object.project.full_repo_name == "rpms/packit"

    packit_yaml = (
        "{'specfile_path': 'packit.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'sync_from_downstream'}],"
        "'downstream_package_name': 'packit'}"
    )
    flexmock(PagureProject).should_receive("get_file_content").with_args(
        path=".distro/source-git.yaml",
        ref="rawhide",
    ).and_raise(FileNotFoundError, "Not found.")
    flexmock(PagureProject).should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref="rawhide",
    ).and_return(packit_yaml)

    assert event_object.packages_config


def test_parse_koji_build_event_completed_rawhide(
    koji_build_completed_rawhide,
    mock_config,
):
    event_object = Parser.parse_event(koji_build_completed_rawhide)

    assert isinstance(event_object, events.result.Build)
    assert event_object.build_id == 1874074
    assert event_object.state == KojiBuildState.complete
    assert event_object.old_state == KojiBuildState.building
    assert event_object.task_id == 80860894
    assert event_object.owner == "packit"
    assert event_object.package_name == "python-ogr"
    assert event_object.commit_sha == "e029dd5250dde9a37a2cdddb6d822d973b09e5da"
    assert event_object.branch_name == "rawhide"
    assert event_object.git_ref == "rawhide"
    assert event_object.epoch is None
    assert event_object.version == "0.34.0"
    assert event_object.release == "1.fc36"
    assert event_object.nvr == "python-ogr-0.34.0-1.fc36"
    assert event_object.project_url == "https://src.fedoraproject.org/rpms/python-ogr"
    assert event_object.start_time is not None
    assert event_object.completion_time is not None

    assert isinstance(event_object.project, PagureProject)
    assert event_object.project.full_repo_name == "rpms/python-ogr"

    packit_yaml = (
        "{'specfile_path': 'python-ogr.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'sync_from_downstream'}],"
        "'downstream_package_name': 'python-ogr'}"
    )
    flexmock(PagureProject).should_receive("get_file_content").with_args(
        path=".distro/source-git.yaml",
        ref="rawhide",
    ).and_raise(FileNotFoundError, "Not found.")
    flexmock(PagureProject).should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref="rawhide",
    ).and_return(packit_yaml)

    assert event_object.packages_config


def test_parse_koji_build_event_completed_f36(koji_build_completed_f36, mock_config):
    event_object = Parser.parse_event(koji_build_completed_f36)

    assert isinstance(event_object, events.result.Build)
    assert event_object.build_id == 1874070
    assert event_object.state == KojiBuildState.complete
    assert event_object.old_state == KojiBuildState.building
    assert event_object.task_id == 80860789
    assert event_object.owner == "packit"
    assert event_object.package_name == "python-ogr"
    assert event_object.commit_sha == "51b57ec04f5e6e9066ac859a1408cfbf1ead307e"
    assert event_object.branch_name == "f36"
    assert event_object.git_ref == "f36"
    assert event_object.epoch is None
    assert event_object.version == "0.34.0"
    assert event_object.release == "1.fc36"
    assert event_object.nvr == "python-ogr-0.34.0-1.fc36"
    assert event_object.project_url == "https://src.fedoraproject.org/rpms/python-ogr"

    assert event_object.start_time is not None
    assert event_object.completion_time is not None

    assert isinstance(event_object.project, PagureProject)
    assert event_object.project.full_repo_name == "rpms/python-ogr"

    packit_yaml = (
        "{'specfile_path': 'python-ogr.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'sync_from_downstream'}],"
        "'downstream_package_name': 'python-ogr'}"
    )
    flexmock(PagureProject).should_receive("get_file_content").with_args(
        path=".distro/source-git.yaml",
        ref="rawhide",
    ).and_raise(FileNotFoundError, "Not found.")
    flexmock(PagureProject).should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref="rawhide",
    ).and_return(packit_yaml)

    assert event_object.packages_config


def test_parse_koji_build_event_completed_epel8(
    koji_build_completed_epel8,
    mock_config,
):
    event_object = Parser.parse_event(koji_build_completed_epel8)

    assert isinstance(event_object, events.result.Build)
    assert event_object.build_id == 1874072
    assert event_object.state == KojiBuildState.complete
    assert event_object.old_state == KojiBuildState.building
    assert event_object.task_id == 80860791
    assert event_object.owner == "packit"
    assert event_object.package_name == "python-ogr"
    assert event_object.commit_sha == "23806a208e32cc937f3a6eb151c62cbbc10d8f96"
    assert event_object.branch_name == "epel8"
    assert event_object.git_ref == "epel8"
    assert event_object.epoch is None
    assert event_object.version == "0.34.0"
    assert event_object.release == "1.el8"
    assert event_object.nvr == "python-ogr-0.34.0-1.el8"
    assert event_object.project_url == "https://src.fedoraproject.org/rpms/python-ogr"

    assert event_object.start_time is not None
    assert event_object.completion_time is not None

    assert isinstance(event_object.project, PagureProject)
    assert event_object.project.full_repo_name == "rpms/python-ogr"

    packit_yaml = (
        "{'specfile_path': 'python-ogr.spec',"
        "'jobs': [{'trigger': 'commit', 'job': 'sync_from_downstream'}],"
        "'downstream_package_name': 'python-ogr'}"
    )
    flexmock(PagureProject).should_receive("get_file_content").with_args(
        path=".distro/source-git.yaml",
        ref="rawhide",
    ).and_raise(FileNotFoundError, "Not found.")
    flexmock(PagureProject).should_receive("get_file_content").with_args(
        path=".packit.yaml",
        ref="rawhide",
    ).and_return(packit_yaml)

    assert event_object.packages_config


def test_parse_koji_tag_event(koji_build_tagged):
    flexmock(KojiHelper).should_receive("get_build_info").with_args(1234567).and_return(
        {"task_id": 7654321},
    )

    event_object = Parser.parse_event(koji_build_tagged)

    assert isinstance(event_object, events.tag.Build)
    assert event_object.build_id == 1234567
    assert event_object.task_id == 7654321
    assert event_object.tag_id == 12345
    assert event_object.tag_name == "f40-build-side-12345"
    assert event_object.owner == "nforro"
    assert event_object.package_name == "python-specfile"
    assert event_object.epoch is None
    assert event_object.version == "0.31.0"
    assert event_object.release == "1.fc40"
    assert event_object.nvr == "python-specfile-0.31.0-1.fc40"
    assert event_object.project_url == "https://src.fedoraproject.org/rpms/python-specfile"

    assert isinstance(event_object.project, PagureProject)
    assert event_object.project.full_repo_name == "rpms/python-specfile"
