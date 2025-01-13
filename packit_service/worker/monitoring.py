# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
import os

from prometheus_client import CollectorRegistry, Counter, Histogram, push_to_gateway

logger = logging.getLogger(__name__)


class Pushgateway:
    def __init__(self):
        self.pushgateway_address = os.getenv(
            "PUSHGATEWAY_ADDRESS",
            "http://pushgateway",
        )
        # so that workers don't overwrite each other's metrics,
        # the job name corresponds to worker name (e.g. packit-worker-0)
        self.worker_name = os.getenv("HOSTNAME")
        self.registry = CollectorRegistry()

        # metrics
        self.copr_builds_queued = Counter(
            "copr_builds_queued",
            "Number of Copr builds queued",
            registry=self.registry,
        )

        self.copr_builds_started = Counter(
            "copr_builds_started",
            "Number of Copr builds started",
            registry=self.registry,
        )

        self.copr_builds_finished = Counter(
            "copr_builds_finished",
            "Number of Copr builds finished",
            registry=self.registry,
        )

        self.test_runs_queued = Counter(
            "test_runs_queued",
            "Number of test runs queued",
            registry=self.registry,
        )

        self.test_runs_started = Counter(
            "test_runs_started",
            "Number of test runs started",
            registry=self.registry,
        )

        self.test_runs_finished = Counter(
            "test_runs_finished",
            "Number of test runs finished",
            registry=self.registry,
        )

        self.no_status_after_25_s = Counter(
            "no_status_after_25_s",
            "Number of PRs/commits with no commit status for more than 25s",
            registry=self.registry,
        )

        self.first_initial_status_time = Histogram(
            "first_initial_status_time",
            "Time it takes to set the initial status for the first check in the first job",
            registry=self.registry,
            buckets=(5, 15, 20, 25, 30, float("inf")),
        )

        self.last_initial_status_time = Histogram(
            "last_initial_status_time",
            "Time it takes to set the initial status for the last check in the last job",
            registry=self.registry,
            buckets=(5, 15, 30, 60, 120, float("inf")),
        )

        self.copr_build_finished_time = Histogram(
            "copr_build_finished_time",
            "Time it takes from setting accepted status for Copr build to finished",
            registry=self.registry,
            buckets=(
                1800,
                3600,
                3 * 3600,
                6 * 3600,
                12 * 3600,
                24 * 3600,
                float("inf"),
            ),
        )

        self.copr_build_end_reported_after_time = Histogram(
            "copr_build_end_reported_after_time",
            "Time it takes from Copr build being finished to reporting the status to user",
            registry=self.registry,
            buckets=(
                60,
                120,
                180,
                300,
                480,
                600,
                1800,
                3600,
                float("inf"),
            ),
        )

        self.copr_build_not_submitted_time = Histogram(
            "copr_build_not_submitted_time",
            "Time it takes from setting accepted status for Copr build to failed status "
            "for an event that prevents submitting of Copr build",
            ["reason"],
            registry=self.registry,
            buckets=(
                1800,
                3600,
                3 * 3600,
                6 * 3600,
                12 * 3600,
                24 * 3600,
                float("inf"),
            ),
        )

        self.test_run_finished_time = Histogram(
            "test_run_finished_time",
            "Time it takes from submitting the test run to set finished status",
            registry=self.registry,
            buckets=(
                1800,
                3600,
                3 * 3600,
                6 * 3600,
                12 * 3600,
                24 * 3600,
                float("inf"),
            ),
        )

        self.events_processed = Counter(
            "events_processed",
            "The number of events processed from the Celery queue",
            registry=self.registry,
        )

        self.events_not_handled = Counter(
            "events_not_handled",
            "The number of events resulting in a Celery task, but then dropped during processing",
            registry=self.registry,
        )

        self.events_pre_check_failed = Counter(
            "events_pre_check_failed",
            "The number of events for which pre_check() failed",
            registry=self.registry,
        )

    def push(self):
        if not (self.pushgateway_address and self.worker_name):
            logger.debug("Pushgateway address or worker name not defined.")
            return

        logger.info("Pushing the metrics to pushgateway.")
        push_to_gateway(
            self.pushgateway_address,
            job=self.worker_name,
            registry=self.registry,
        )
