# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
import datetime
from enum import Enum

CONTACTS_URL = "https://packit.dev/#contact"
DOCS_URL = "https://packit.dev/docs"
DOCS_CONFIGURATION_URL = f"{DOCS_URL}/configuration"
DOCS_FAQ_URL = f"{DOCS_URL}/faq"
DOCS_HOW_TO_RETRIGGER_URL = (
    f"{DOCS_URL}/guide/#how-to-re-trigger-packit-actions-in-your-pull-request"
)
DOCS_HOW_TO_CONFIGURE_URL = f"{DOCS_URL}/guide/#3-configuration"
DOCS_APPROVAL_URL = f"{DOCS_URL}/guide/#2-approval"
DOCS_VM_IMAGE_BUILD = f"{DOCS_URL}/cli/build/in-image-builder/"

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
    "You can retrigger the {job} by adding a comment (`{packit_comment_command_prefix} {command}`) "
    "into this {place}."
)
COPR_CHROOT_CHANGE_MSG = (
    "Settings of a Copr project {owner}/{project} need to be updated, "
    "but Packit can't do that when there are previous builds still in progress.\n"
    "You should be able to resolve the problem by recreating this pull request "
    "or running `{packit_comment_command_prefix} build` after all builds finished.\n\n"
    "This was the change Packit tried to do:\n\n"
    "{table}"
    "\n"
)

NAMESPACE_NOT_ALLOWED_MARKDOWN_DESCRIPTION = (
    "In order to start using the service, "
    "your repository or namespace needs to be allowed. "
    "We are now onboarding Fedora contributors who have "
    "a valid [Fedora Account System](https://fedoraproject.org/wiki/Account_System) account.\n\n"
    "{instructions}"
    "For more details on how to get allowed for our service, please read "
    f"the instructions [in our onboarding guide]({DOCS_APPROVAL_URL})."
)

NAMESPACE_NOT_ALLOWED_MARKDOWN_ISSUE_INSTRUCTIONS = (
    "Packit has opened [an issue]({issue_url}) for you to finish the approval process. "
    "The process is automated and all the information can be found "
    "in the linked issue.\n\n"
)
NOTIFICATION_REPO = "https://github.com/packit/notifications"


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

DEFAULT_RETRY_LIMIT = 2
# Retry more times for outages
DEFAULT_RETRY_LIMIT_OUTAGE = 5
# retry in 3s, 6s
DEFAULT_RETRY_BACKOFF = 3
RETRY_INTERVAL_IN_MINUTES_WHEN_USER_ACTION_IS_NEEDED = 10
BASE_RETRY_INTERVAL_IN_MINUTES_FOR_OUTAGES = 1
BASE_RETRY_INTERVAL_IN_SECONDS_FOR_INTERNAL_ERRORS = 10

# Time after which we no longer check the status of jobs and consider it as
# timeout/internal error. Nothing should hopefully run for 7 days.
DEFAULT_JOB_TIMEOUT = 7 * 24 * 3600

# SRPM builds older than this number of days are considered
# outdated and their logs can be discarded.
SRPMBUILDS_OUTDATED_AFTER_DAYS = 30

DATE_OF_DEFAULT_SRPM_BUILD_IN_COPR = datetime.datetime(
    year=2022,
    month=9,
    day=6,
    hour=6,
    minute=0,
    microsecond=0,
    tzinfo=datetime.timezone.utc,
)

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
    "via `{packit_comment_command_prefix} test` comment.*",
)

INTERNAL_TF_BUILDS_AND_TESTS_NOT_ALLOWED = (
    "{actor} can't run tests (and builds) internally",
    "*As a project maintainer, you can trigger the build and test jobs manually "
    "via `{packit_comment_command_prefix} build` comment "
    "or only test job via `{packit_comment_command_prefix} test` comment.*",
)

CUSTOM_COPR_PROJECT_NOT_ALLOWED_STATUS = (
    "Not allowed to build in {copr_project} Copr project."
)
CUSTOM_COPR_PROJECT_NOT_ALLOWED_CONTENT = (
    "Your git-forge project is not allowed to use "
    "the configured `{copr_project}` Copr project.\n\n"
    "Please, add this git-forge project `{forge_project}` "
    "to `Packit allowed forge projects`"
    "in the [Copr project settings]({copr_settings_url}#packit_forge_projects_allowed). "
)

CUSTOM_COPR_PROJECT_ALLOWED_IN_PACKIT_CONFIG = (
    "Your git-forge project `{forge_project}` has permissions "
    "to build in `{copr_project}` Copr project configured in Packit. "
    "However, we migrated to the solution where you can configure "
    "the allowed git-forge projects in Copr yourself and will remove the configuration "
    "in Packit for the allowed projects soon. "
    "Therefore, please, add this git-forge project `{forge_project}` "
    "to `Packit allowed forge projects`"
    "in the [Copr project settings]({copr_settings_url}#packit_forge_projects_allowed). "
)

FASJSON_URL = "https://fasjson.fedoraproject.org"

PACKIT_VERIFY_FAS_COMMAND = "verify-fas"

MISSING_PERMISSIONS_TO_BUILD_IN_COPR = (
    "You don't have permissions to build in this copr."
)
NOT_ALLOWED_TO_BUILD_IN_COPR = "is not allowed to build in the copr"
GIT_FORGE_PROJECT_NOT_ALLOWED_TO_BUILD_IN_COPR = "can't build in this Copr via Packit."

GITLAB_ISSUE = (
    "To configure Packit you need to add secret for a webhook [here]({url}/hooks).\n\n"
    "Click on `Edit` next to a Packit webhook you have configured and fill in "
    "the following _Secret token_ to authenticate requests coming to Packit:\n"
    "```\n"
    "{token_project}\n"
    "```\n\n"
    "Or if you want to configure a _Group Hook_ (GitLab EE) the _Secret token_ would be:\n"
    "```\n"
    "{token_group}\n"
    "```\n\n"
    "Packit also needs rights to set commit statuses to merge requests. Please, "
    "grant `{packit_user}` user `Developer` permissions on the `{namespace}/{repo}`"
    " repository. You can do so [here]({url}/-/project_members)."
)
