# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
from datetime import datetime, timedelta
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
DOCS_TESTING_FARM = f"{DOCS_URL}/configuration/upstream/tests"
DOCS_VALIDATE_CONFIG = f"{DOCS_URL}/cli/config/validate"
DOCS_VALIDATE_HOOKS = "https://packit.dev/posts/pre-commit-hooks#validate-config"

KOJI_PRODUCTION_BUILDS_ISSUE = "https://pagure.io/releng/issue/9801"

SANDCASTLE_WORK_DIR = "/tmp/sandcastle"
SANDCASTLE_IMAGE = "quay.io/packit/sandcastle"
SANDCASTLE_DEFAULT_PROJECT = "myproject"
SANDCASTLE_PVC = "SANDCASTLE_PVC"

CONFIG_FILE_NAME = "packit-service.yaml"

TESTING_FARM_API_URL = "https://api.dev.testing-farm.io/v0.1/"
TESTING_FARM_INSTALLABILITY_TEST_URL = "https://gitlab.com/testing-farm/tests"
TESTING_FARM_INSTALLABILITY_TEST_REF = "main"
TESTING_FARM_EXTRA_PARAM_MERGED_SUBTREES = (
    "environments",
    "test",
    "settings",
)
TESTING_FARM_ARTIFACTS_KEY = "artifacts"

MSG_DOWNSTREAM_JOB_ERROR_HEADER = (
    "Packit failed on creating {object} in dist-git "
    "({dist_git_url}):\n\n"
    "<table>"
    "<tr>"
    "<th>dist-git branch</th>"
    "<th>error</th>"
    "</tr>"
)

MSG_DOWNSTREAM_JOB_ERROR_ROW = (
    '<tr><td><code>{branch}</code></td><td>See <a href="{url}">{url}</a></td></tr>\n'
)

MSG_GET_IN_TOUCH = f"\n\n---\n\n*Get in [touch with us]({CONTACTS_URL}) if you need some help.*"

MSG_RETRIGGER = (
    "You can retrigger the {job} by adding a comment (`{packit_comment_command_prefix} {command}`) "
    "into this {place}."
)

MSG_RETRIGGER_DISTGIT = (
    "You can retrigger the {job} by adding a comment (`{packit_comment_command_prefix} {command}`) "
    "into any open open pull request in dist-git."
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
    "Only users with write or admin permissions to the repository can trigger Packit-as-a-Service"
)

TASK_ACCEPTED = "The task was accepted."

COPR_SRPM_CHROOT = "srpm-builds"
COPR_SUCC_STATE = "succeeded"
COPR_FAIL_STATE = "failed"
COPR_API_SUCC_STATE = 1
COPR_API_FAIL_STATE = 2

# Retry 2 times
DEFAULT_RETRY_LIMIT = 2
# Retry more times for outages
DEFAULT_RETRY_LIMIT_OUTAGE = 5
# retry in 0-7s, 0-14s, 0-28s, 0-48s, 0-96s
# because jitter is enabled by default, celery makes these retries random:
# https://docs.celeryq.dev/en/latest/userguide/tasks.html#Task.retry_jitter

RETRY_LIMIT_RELEASE_ARCHIVE_DOWNLOAD_ERROR = 6

DEFAULT_RETRY_BACKOFF = 7
BASE_RETRY_INTERVAL_IN_MINUTES_FOR_OUTAGES = 1
BASE_RETRY_INTERVAL_IN_SECONDS_FOR_INTERNAL_ERRORS = 10

# Time after which we no longer check the status of jobs and consider it as
# timeout/internal error. Nothing should hopefully run for 7 days.
DEFAULT_JOB_TIMEOUT = 7 * 24 * 3600

# SRPM builds older than this number of days are considered
# outdated and their logs can be discarded.
SRPMBUILDS_OUTDATED_AFTER_DAYS = 30

PACKAGE_CONFIGS_OUTDATED_AFTER_DAYS = 1

ALLOWLIST_CONSTANTS = {
    "approved_automatically": "approved_automatically",
    "waiting": "waiting",
    "approved_manually": "approved_manually",
    "denied": "denied",
}

CELERY_TASK_DEFAULT_QUEUE = "short-running"

CELERY_DEFAULT_MAIN_TASK_NAME = "task.steve_jobs.process_message"

MSG_TABLE_HEADER_WITH_DETAILS = "| Name/Job | URL |\n| --- | --- |\n"

DEFAULT_MAPPING_TF = {
    "epel-6": "centos-6",
    "epel-7": "centos-7",
    "epel-8": "centos-stream-8",
    "epel-9": "centos-stream-9",
    "epel-10": "centos-stream-10",
}

DEFAULT_MAPPING_INTERNAL_TF = {
    "epel-6": "rhel-6",
    "epel-7": "rhel-7",
    "epel-8": "rhel-8",
    "epel-9": "centos-stream-9",
    "epel-10": "centos-stream-10",
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

CUSTOM_COPR_PROJECT_NOT_ALLOWED_STATUS = "Not allowed to build in {copr_project} Copr project."
CUSTOM_COPR_PROJECT_NOT_ALLOWED_CONTENT = (
    "Your git-forge project is not allowed to use "
    "the configured `{copr_project}` Copr project.\n\n"
    "Please, add this git-forge project `{forge_project}` "
    "to `Packit allowed forge projects`"
    "in the [Copr project settings]({copr_settings_url}#packit_forge_projects_allowed). "
)

FASJSON_URL = "https://fasjson.fedoraproject.org"

PACKIT_VERIFY_FAS_COMMAND = "verify-fas"

MISSING_PERMISSIONS_TO_BUILD_IN_COPR = "You don't have permissions to build in this copr."
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

DASHBOARD_JOBS_TESTING_FARM_PATH = "/jobs/testing-farm-runs"

# https://docs.testing-farm.io/general/0.1/test-environment.html#_supported_architectures
TESTING_FARM_SUPPORTED_ARCHS: dict[str, list[str]] = {
    "public": [
        "aarch64",
        "x86_64",
    ],
    "redhat": [
        "aarch64",
        "ppc64le",
        "s390x",
        "x86_64",
    ],
}

# [NOTE] for backwards-compatibility with existing references
PUBLIC_TF_ARCHITECTURE_LIST = TESTING_FARM_SUPPORTED_ARCHS["public"]
INTERNAL_TF_ARCHITECTURE_LIST = TESTING_FARM_SUPPORTED_ARCHS["redhat"]

DENIED_MSG = (
    f"You were denied from using Packit Service. If you think this was done by mistake, "
    f"please, [let us know]({CONTACTS_URL})."
)

# We want to be able to access both
# upstream and downstream repos through the
# shared sandcastle dir
SANDCASTLE_DG_REPO_DIR = "dist-git"
SANDCASTLE_LOCAL_PROJECT_DIR = "local-project"

FAILURE_COMMENT_MESSAGE_VARIABLES = {
    # placeholder name in the user customized failure message:
    # default value for placeholder if not given
    placeholder: f"{{no entry for {placeholder}}}"
    for placeholder in (
        "commit_sha",
        "packit_dashboard_url",
        "external_dashboard_url",
        "logs_url",
    )
}

USAGE_CURRENT_DATE = datetime.now().replace(minute=0, second=0, microsecond=0)
USAGE_PAST_DAY_DATE_STR = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
USAGE_PAST_WEEK_DATE_STR = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
USAGE_PAST_MONTH_DATE_STR = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
USAGE_PAST_YEAR_DATE_STR = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
USAGE_DATE_IN_THE_PAST = USAGE_CURRENT_DATE.replace(year=USAGE_CURRENT_DATE.year - 100)
USAGE_DATE_IN_THE_PAST_STR = USAGE_DATE_IN_THE_PAST.strftime("%Y-%m-%d")

OPEN_SCAN_HUB_FEATURE_DESCRIPTION = (
    ":warning: You can see the list of known issues and also provide your feedback"
    " [here](https://github.com/packit/packit/discussions/2371). \n\n"
    "You can disable the scanning in your configuration by "
    "setting `osh_diff_scan_after_copr_build` to `false`. For more information, "
    f"see [docs]({DOCS_URL}/configuration#osh_diff_scan_after_copr_build)."
)
