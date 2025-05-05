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
from packit_service.worker.handlers.copr import (
    CoprBuildEndHandler,
    CoprBuildHandler,
    CoprBuildStartHandler,
)
from packit_service.worker.handlers.distgit import (
    ProposeDownstreamHandler,
    SyncFromDownstream,
)
from packit_service.worker.handlers.forges import (
    GithubAppInstallationHandler,
    GithubFasVerificationHandler,
)
from packit_service.worker.handlers.koji import (
    KojiBuildHandler,
    KojiTaskReportHandler,
)
from packit_service.worker.handlers.open_scan_hub import (
    CoprOpenScanHubTaskFinishedHandler,
    CoprOpenScanHubTaskStartedHandler,
)
from packit_service.worker.handlers.testing_farm import (
    DownstreamTestingFarmHandler,
    DownstreamTestingFarmResultsHandler,
    TestingFarmHandler,
    TestingFarmResultsHandler,
)
from packit_service.worker.handlers.vm_image import (
    VMImageBuildHandler,
    VMImageBuildResultHandler,
)

__all__ = [
    Handler.__name__,
    JobHandler.__name__,
    CoprBuildHandler.__name__,
    CoprBuildEndHandler.__name__,
    CoprBuildStartHandler.__name__,
    GithubAppInstallationHandler.__name__,
    SyncFromDownstream.__name__,
    ProposeDownstreamHandler.__name__,
    KojiBuildHandler.__name__,
    KojiTaskReportHandler.__name__,
    DownstreamTestingFarmHandler.__name__,
    DownstreamTestingFarmResultsHandler.__name__,
    TestingFarmHandler.__name__,
    TestingFarmResultsHandler.__name__,
    GithubFasVerificationHandler.__name__,
    VMImageBuildHandler.__name__,
    VMImageBuildResultHandler.__name__,
    CoprOpenScanHubTaskFinishedHandler.__name__,
    CoprOpenScanHubTaskStartedHandler.__name__,
]
