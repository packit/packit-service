# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from typing import Dict, Optional

from packit.config import JobConfigTriggerType, JobConfig

from packit_service.service.events import TheJobTriggerType

JOB_TRIGGER_TO_CONFIG_MAPPING: Dict[
    TheJobTriggerType, Optional[JobConfigTriggerType]
] = {
    TheJobTriggerType.commit: JobConfigTriggerType.commit,
    TheJobTriggerType.release: JobConfigTriggerType.release,
    TheJobTriggerType.pull_request: JobConfigTriggerType.pull_request,
    TheJobTriggerType.push: JobConfigTriggerType.commit,
    TheJobTriggerType.pr_comment: JobConfigTriggerType.pull_request,
}


def is_trigger_matching_job_config(
    trigger: TheJobTriggerType, job_config: JobConfig
) -> bool:
    """
    Check that the event trigger matches the one from config.

    We can have multiple events for one config.
    e.g. Both pr_comment and pull_request are compatible
         with the pull_request config in the config
    """
    config_trigger = JOB_TRIGGER_TO_CONFIG_MAPPING.get(trigger)
    return bool(config_trigger and job_config.trigger == config_trigger)
