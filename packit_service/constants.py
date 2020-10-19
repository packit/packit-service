from enum import Enum

DOCS_URL = "https://packit.dev/packit-as-a-service/"
FAQ_URL = f"{DOCS_URL}#faq"
FAQ_URL_HOW_TO_RETRIGGER = (
    f"{DOCS_URL}#how-to-re-trigger-packit-service-actions-in-your-pull-request"
)
KOJI_PRODUCTION_BUILDS_ISSUE = "https://pagure.io/releng/issue/9801"

SANDCASTLE_WORK_DIR = "/sandcastle"
SANDCASTLE_IMAGE = "docker.io/usercont/sandcastle"
SANDCASTLE_DEFAULT_PROJECT = "myproject"
SANDCASTLE_PVC = "SANDCASTLE_PVC"

CONFIG_FILE_NAME = "packit-service.yaml"

TESTING_FARM_TRIGGER_URL = (
    "https://scheduler-testing-farm.apps.ci.centos.org/v0/trigger"
)

MSG_RETRIGGER = (
    "You can re-trigger build by adding a comment (`/packit {build}`) "
    "into this pull request."
)

FILE_DOWNLOAD_FAILURE = "Failed to download file from URL"

PERMISSIONS_ERROR_WRITE_OR_ADMIN = (
    "Only users with write or admin permissions to the repository "
    "can trigger Packit-as-a-Service"
)

COPR_SUCC_STATE = "succeeded"
COPR_FAILED_STATE = "failed"
COPR_API_SUCC_STATE = 1
COPR_API_FAIL_STATE = 2

PG_COPR_BUILD_STATUS_FAILURE = "failure"
PG_COPR_BUILD_STATUS_SUCCESS = "success"

RETRY_LIMIT = 5

WHITELIST_CONSTANTS = {
    "approved_automatically": "approved_automatically",
    "waiting": "waiting",
    "approved_manually": "approved_manually",
}


class KojiBuildState(Enum):
    """
    Koji states used in fedmsg payloads.

    Sometimes, koji use numbers instead,
    but we don't need them yet.
    Corresponding numbers are as comments if anyone needs them.
    """

    free = "FREE"  # 0
    open = "OPEN"  # 1
    closed = "CLOSED"  # 2
    canceled = "CANCELED"  # 3
    assigned = "ASSIGNED"  # 4
    failed = "FAILED"  # 5
