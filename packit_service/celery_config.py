# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
from celery.schedules import crontab

import packit_service.constants

# https://docs.celeryq.dev/en/stable/userguide/tasks.html#ignore-results-you-don-t-want
task_ignore_result = True

# https://docs.celeryq.dev/en/stable/userguide/configuration.html#std-setting-task_default_queue
task_default_queue = packit_service.constants.CELERY_TASK_DEFAULT_QUEUE

# https://docs.celeryq.dev/en/stable/userguide/periodic-tasks.html
beat_schedule = {
    "update-pending-copr-builds": {
        "task": "packit_service.worker.tasks.babysit_pending_copr_builds",
        "schedule": 3600.0,
        "options": {"queue": "long-running"},
    },
    "update-pending-tft-runs": {
        "task": "packit_service.worker.tasks.babysit_pending_tft_runs",
        "schedule": 600.0,
        "options": {"queue": "long-running"},
    },
    "database-maintenance": {
        "task": "packit_service.worker.tasks.database_maintenance",
        "schedule": crontab(minute=0, hour=1),  # nightly at 1AM
        "options": {"queue": "long-running"},
    },
}

# http://mher.github.io/flower/prometheus-integration.html#set-up-your-celery-application
worker_send_task_events = True
task_send_sent_event = True
