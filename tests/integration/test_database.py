from datetime import datetime

from flexmock import flexmock

from packit_service.models import SRPMBuildModel
from packit_service.worker.database import discard_old_srpm_build_logs


def test_cleanup_old_srpm_build_logs():
    srpm_build = flexmock(id=1, build_submitted_time=datetime.utcnow())
    flexmock(srpm_build).should_receive("set_logs").with_args(None).once()
    flexmock(srpm_build).should_receive("set_url").with_args(None).once()
    flexmock(SRPMBuildModel).should_receive("get_older_than").and_return(
        [srpm_build]
    ).once()
    discard_old_srpm_build_logs()
