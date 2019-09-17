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
import logging
from typing import Optional

from celery.task import Task as CeleryTask

from packit_service.celerizer import celery_app
from packit_service.service.models import Task
from packit_service.worker.jobs import SteveJobs

# debug logs of these are super-duper verbose
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("github").setLevel(logging.WARNING)
logging.getLogger("kubernetes").setLevel(logging.WARNING)
# info is just enough
logging.getLogger("ogr").setLevel(logging.INFO)
# easier debugging
logging.getLogger("packit").setLevel(logging.DEBUG)
logging.getLogger("sandcastle").setLevel(logging.DEBUG)


@celery_app.task(bind=True, name="task.steve_jobs.process_message")
def process_message(self: CeleryTask, event: dict, topic: str = None) -> Optional[dict]:
    # storing the whole event may be an overkill
    Task.create(celery_id=self.request.id, metadata=event)
    return SteveJobs().process_message(event=event, topic=topic)
