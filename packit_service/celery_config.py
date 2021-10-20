# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import packit_service.constants

# https://docs.celeryproject.org/en/stable/userguide/tasks.html#ignore-results-you-don-t-want
task_ignore_result = True

# https://docs.celeryproject.org/en/latest/userguide/configuration.html#std-setting-task_default_queue
task_default_queue = packit_service.constants.CELERY_TASK_DEFAULT_QUEUE
