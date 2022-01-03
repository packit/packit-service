from datetime import timedelta, datetime
from logging import getLogger
from os import getenv

from packit_service.constants import SRPMBUILDS_OUTDATED_AFTER_DAYS
from packit_service.models import SRPMBuildModel

logger = getLogger(__name__)


def discard_old_srpm_build_logs():
    """Called periodically (see celery_config.py) to discard logs of old SRPM builds."""
    logger.debug("About to discard old SRPM build logs & artifact urls.")
    outdated_after_days = getenv(
        "SRPMBUILDS_OUTDATED_AFTER_DAYS", SRPMBUILDS_OUTDATED_AFTER_DAYS
    )
    ago = timedelta(days=int(outdated_after_days))
    for build in SRPMBuildModel.get_older_than(ago):
        age = datetime.utcnow() - build.build_submitted_time
        logger.debug(
            f"SRPM build {build.id}, age '{age}' is older than '{ago}'. "
            "Discarding log and artifact url."
        )
        build.set_logs(None)
        build.set_url(None)
