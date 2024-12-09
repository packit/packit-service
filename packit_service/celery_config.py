# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
from celery.schedules import crontab

import packit_service.constants

# https://docs.celeryq.dev/en/stable/userguide/configuration.html#std-setting-task_default_queue
task_default_queue = packit_service.constants.CELERY_TASK_DEFAULT_QUEUE
# https://docs.celeryq.dev/en/latest/userguide/configuration.html#conf-redis-result-backend
result_backend = "redis"

# do not store task results by default
# https://docs.celeryq.dev/en/latest/userguide/configuration.html#task-ignore-result
task_ignore_result = True

imports = ("packit_service.worker.tasks", "packit_service.service.tasks")

task_routes = {
    "task.babysit_vm_image_build": "long-running",
    "task.babysit_copr_build": "long-running",
    "packit_service.service.tasks.get_past_usage_data": "long-running",
    "packit_service.service.tasks.get_usage_interval_data": "long-running",
}

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
    "update-pending-vm-image-builds": {
        "task": "packit_service.worker.tasks.babysit_pending_vm_image_builds",
        "schedule": 3600.0,
        "options": {"queue": "long-running"},
    },
    "database-maintenance": {
        "task": "packit_service.worker.tasks.database_maintenance",
        "schedule": crontab(minute=0, hour=1),  # nightly at 1AM
        "options": {"queue": "long-running", "time_limit": 1800},
    },
    "check-onboarded-projects": {
        "task": "packit_service.worker.tasks.run_check_onboarded_projects",
        "schedule": crontab(minute=0, hour=2),  # nightly at 2AM
        "options": {"queue": "long-running"},
    },
    "get_usage_statistics": {
        "task": "packit_service.worker.tasks.get_usage_statistics",
        "schedule": 10800.0,
        "options": {"queue": "long-running", "time_limit": 3600},
    },
}

# http://mher.github.io/flower/prometheus-integration.html#set-up-your-celery-application
worker_send_task_events = True
task_send_sent_event = True

# https://docs.celeryq.dev/en/stable/userguide/configuration.html#std-setting-task_time_limit
task_time_limit = 900  # 15 min
