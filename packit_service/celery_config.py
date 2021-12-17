# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
from celery.schedules import crontab

import packit_service.constants

# https://docs.celeryproject.org/en/stable/userguide/tasks.html#ignore-results-you-don-t-want
task_ignore_result = True

# https://docs.celeryproject.org/en/latest/userguide/configuration.html#std-setting-task_default_queue
task_default_queue = packit_service.constants.CELERY_TASK_DEFAULT_QUEUE

# https://docs.celeryproject.org/en/stable/userguide/periodic-tasks.html
beat_schedule = {
    "update-pending-copr-builds": {
        "task": "packit_service.worker.tasks.babysit_pending_copr_builds",
        "schedule": 3600.0,
    },
    "update-pending-tft-runs": {
        "task": "packit_service.worker.tasks.babysit_pending_tft_runs",
        "schedule": 600.0,
    },
    "database-discard-old-stuff": {
        "task": "packit_service.worker.tasks.periodic_database_cleanup",
        "schedule": crontab(minute=0, hour=1),  # daily at 1AM
    },
}
