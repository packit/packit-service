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

"""
Accept a message from commandline and process it directly - bypass celery
"""
import json
import logging
import sys
from pathlib import Path

import click

from packit_service.worker.jobs import SteveJobs

logger = logging.getLogger(__name__)


@click.command("process-message")
@click.argument("path", nargs=1, required=False)
def process_message(path):
    """
    Accept a message from commandline and process it directly - bypass celery

    Either provide a filename with the message or pipe it:
      cat event.json | packit-service process-message

    if MESSAGE-ID is specified, process only the selected messages
    """
    if path:
        logger.info(f"reading the message from file {path}")
        event = json.loads(Path(path).read_text())
    else:
        logger.info("reading the message from stdin")
        event = sys.stdin.read()
    SteveJobs().process_message(event=event)
