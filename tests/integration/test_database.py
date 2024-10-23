# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from datetime import datetime, timezone
from pathlib import Path

from boto3.s3.transfer import S3Transfer
from flexmock import flexmock

from packit_service.models import ProjectEventModel, SRPMBuildModel
from packit_service.worker import database


def test_cleanup_old_srpm_build_logs():
    srpm_build = flexmock(id=1, build_submitted_time=datetime.now(timezone.utc))
    flexmock(srpm_build).should_receive("set_logs").with_args(None).once()
    flexmock(srpm_build).should_receive("set_url").with_args(None).once()
    flexmock(SRPMBuildModel).should_receive("get_older_than").and_return(
        [srpm_build],
    ).once()
    database.discard_old_srpm_build_logs()


def test_discard_old_package_configs():
    event_model1 = flexmock(id=1)
    event_model2 = flexmock(id=2)
    flexmock(ProjectEventModel).should_receive(
        "get_and_reset_older_than_with_packages_config",
    ).and_return([event_model1, event_model2]).once()
    database.discard_old_package_configs()


def test_backup():
    flexmock(database).should_receive("is_aws_configured").once().and_return(True)
    flexmock(database).should_receive("dump_to").once()
    flexmock(database).should_receive("gzip_file").once().and_return(Path("xyz"))
    flexmock(S3Transfer).should_receive("upload_file").once()
    flexmock(Path).should_receive("unlink").twice()
    database.backup()
