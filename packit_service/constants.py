# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from enum import Enum

DOCS_URL = "https://packit.dev/docs"
CONTACTS_URL = "https://packit.dev/#contact"
FAQ_URL = f"{DOCS_URL}/faq"
FAQ_URL_HOW_TO_RETRIGGER = (
    f"{DOCS_URL}/packit-as-a-service/"
    "#how-to-re-trigger-packit-service-actions-in-your-pull-request"
)
KOJI_PRODUCTION_BUILDS_ISSUE = "https://pagure.io/releng/issue/9801"

SANDCASTLE_WORK_DIR = "/tmp/sandcastle"
SANDCASTLE_IMAGE = "quay.io/packit/sandcastle"
SANDCASTLE_DEFAULT_PROJECT = "myproject"
SANDCASTLE_PVC = "SANDCASTLE_PVC"

CONFIG_FILE_NAME = "packit-service.yaml"

TESTING_FARM_API_URL = "https://api.dev.testing-farm.io/v0.1/"
TESTING_FARM_INSTALLABILITY_TEST_URL = "https://gitlab.com/testing-farm/tests"
TESTING_FARM_INSTALLABILITY_TEST_REF = "main"

MSG_RETRIGGER = (
    "You can retrigger the {job} by adding a comment (`/packit {command}`) "
    "into this {place}."
)
COPR_CHROOT_CHANGE_MSG = (
    "Settings of a Copr project {owner}/{project} need to be updated, "
    "but Packit can't do that when there are previous builds still in progress.\n"
    "You should be able to resolve the problem by recreating this pull request "
    "or running `/packit build` after all builds finished.\n\n"
    "This was the change Packit tried to do:\n\n"
    "{table}"
    "\n"
)

FILE_DOWNLOAD_FAILURE = "Failed to download file from URL"

PERMISSIONS_ERROR_WRITE_OR_ADMIN = (
    "Only users with write or admin permissions to the repository "
    "can trigger Packit-as-a-Service"
)

TASK_ACCEPTED = "The task was accepted."

COPR_SRPM_CHROOT = "srpm-builds"
COPR_SUCC_STATE = "succeeded"
COPR_FAILED_STATE = "failed"
COPR_API_SUCC_STATE = 1
COPR_API_FAIL_STATE = 2

PG_BUILD_STATUS_FAILURE = "failure"
PG_BUILD_STATUS_SUCCESS = "success"

DEFAULT_RETRY_LIMIT = 2
# retry in 3s, 6s
DEFAULT_RETRY_BACKOFF = 3

# Time after which we no longer check the status of jobs and consider it as
# timeout/internal error. Nothing should hopefully run for 7 days.
DEFAULT_JOB_TIMEOUT = 7 * 24 * 3600

# SRPM builds older than this number of days are considered
# outdated and their logs can be discarded.
SRPMBUILDS_OUTDATED_AFTER_DAYS = 90

ALLOWLIST_CONSTANTS = {
    "approved_automatically": "approved_automatically",
    "waiting": "waiting",
    "approved_manually": "approved_manually",
    "denied": "denied",
}

CELERY_TASK_DEFAULT_QUEUE = "short-running"

CELERY_DEFAULT_MAIN_TASK_NAME = "task.steve_jobs.process_message"

MSG_MORE_DETAILS = "You can find more details about the job [here]({url}).\n\n"

MSG_TABLE_HEADER_WITH_DETAILS = "| Name/Job | URL |\n" "| --- | --- |\n"

REQUESTED_PULL_REQUEST_COMMENT = "/packit"

# https://github.com/packit/sandcastle/blob/3bd64e0812e3981b5462601e049471854eec433a/files/install-rpm-packages.yaml#L6
SRPM_BUILD_DEPS = [
    "python3-pip",
    "python3-setuptools",
    "python3-setuptools_scm",
    "python3-setuptools_scm_git_archive",
    "rsync",
    "make",
    "git-core",
    "rpmdevtools",
    "automake",
    "autoconf",
    "libtool",
    "tito",
    "cmake",
    "meson",
    "ninja-build",
    "wget",
    "curl",
    "findutils",
    "which",
    "sed",
    "gawk",
    "python3-docutils",
    "python3-wheel",
    "json-c-devel",
    "systemd-devel",
    "libcurl-devel",
    "python3-zanata-client",
    "rust",
    "cargo",
    "rubygems",
    "npm",
    "selinux-policy",
    "glib2-devel",
    "gettext-devel",
    "python3-polib",
    "gobject-introspection-devel",
    "glade-devel",
    "libxklavier-devel",
    "libarchive-devel",
    "rpm-devel",
    "audit-libs-devel",
    "nss_wrapper",
    "fmf",
]

DEFAULT_MAPPING_TF = {
    "epel-6": "centos-6",
    "epel-7": "centos-7",
    "epel-8": "centos-stream-8",
    "epel-9": "centos-stream-9",
}

DEFAULT_MAPPING_INTERNAL_TF = {
    "epel-6": "rhel-6",
    "epel-7": "rhel-7",
    "epel-8": "rhel-8",
    "epel-9": "centos-stream-9",
}

COMMENT_REACTION = "eyes"


class KojiTaskState(Enum):
    """
    Koji states used in fedmsg payloads
    for buildsys.task.state.change
    used for scratch builds.
    """

    free = "FREE"  # 0
    open = "OPEN"  # 1
    closed = "CLOSED"  # 2
    canceled = "CANCELED"  # 3
    assigned = "ASSIGNED"  # 4
    failed = "FAILED"  # 5

    @staticmethod
    def from_number(number: int):
        return {
            0: KojiTaskState.free,
            1: KojiTaskState.open,
            2: KojiTaskState.closed,
            3: KojiTaskState.canceled,
            4: KojiTaskState.assigned,
            5: KojiTaskState.failed,
        }.get(number)


class KojiBuildState(Enum):
    """
    Koji states used in fedmsg payloads
    for buildsys.build.state.change
    used for prod (=non scratch) builds.
    """

    building = "BUILDING"  # 0
    complete = "COMPLETE"  # 1
    deleted = "DELETED"  # 2
    failed = "FAILED"  # 3
    canceled = "CANCELED"  # 4

    @staticmethod
    def from_number(number: int):
        return {
            0: KojiBuildState.building,
            1: KojiBuildState.complete,
            2: KojiBuildState.deleted,
            3: KojiBuildState.failed,
            4: KojiBuildState.canceled,
        }.get(number)


INTERNAL_TF_TESTS_NOT_ALLOWED = (
    "{actor} can't run tests internally",
    "*As a project maintainer, you can trigger the test job manually "
    "via `/packit test` comment.*",
)

INTERNAL_TF_BUILDS_AND_TESTS_NOT_ALLOWED = (
    "{actor} can't run tests (and builds) internally",
    "*As a project maintainer, you can trigger the build and test jobs manually "
    "via `/packit build` comment.*",
)
