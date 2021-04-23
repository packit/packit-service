# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
import os

from prometheus_client import CollectorRegistry, Counter, push_to_gateway

logger = logging.getLogger(__name__)


class Pushgateway:
    def __init__(self):
        self.pushgateway_address = os.getenv(
            "PUSHGATEWAY_ADDRESS", "http://pushgateway"
        )
        # so that workers don't overwrite each other's metrics,
        # the job name corresponds to worker name (e.g. packit-worker-0)
        self.worker_name = os.getenv("HOSTNAME")
        self.registry = CollectorRegistry()

        # metrics
        self.copr_builds = Counter(
            "copr_builds",
            "Number of Copr builds created",
            registry=self.registry,
        )

    def push(self):
        if not (self.pushgateway_address and self.worker_name):
            logger.debug("Pushgateway address or worker name not defined.")
            return

        push_to_gateway(
            self.pushgateway_address, job=self.worker_name, registry=self.registry
        )

    def push_copr_build_created(self):
        self.copr_builds.inc()
        self.push()
