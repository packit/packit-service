# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

# If you have some problems with the imports between files in this directory,
# try using absolute import.
# Example:
# from packit_service.worker.handlers.fedmsg import something
# instead of
# from packit_service.worker.handlers import something


from packit_service.worker.handlers.abstract import (
    Handler,
    JobHandler,
)
from packit_service.worker.handlers.bugzilla import (
    BugzillaHandler,
)
from packit_service.worker.handlers.copr import (
    CoprBuildHandler,
    CoprBuildEndHandler,
    CoprBuildStartHandler,
)
from packit_service.worker.handlers.distgit import (
    SyncFromDownstream,
    ProposeDownstreamHandler,
)
from packit_service.worker.handlers.forges import (
    GithubAppInstallationHandler,
)
from packit_service.worker.handlers.koji import (
    KojiBuildHandler,
    KojiBuildReportHandler,
)
from packit_service.worker.handlers.testing_farm import (
    TestingFarmHandler,
    TestingFarmResultsHandler,
)

__all__ = [
    Handler.__name__,
    JobHandler.__name__,
    BugzillaHandler.__name__,
    CoprBuildHandler.__name__,
    CoprBuildEndHandler.__name__,
    CoprBuildStartHandler.__name__,
    GithubAppInstallationHandler.__name__,
    SyncFromDownstream.__name__,
    ProposeDownstreamHandler.__name__,
    KojiBuildHandler.__name__,
    KojiBuildReportHandler.__name__,
    TestingFarmHandler.__name__,
    TestingFarmResultsHandler.__name__,
]
