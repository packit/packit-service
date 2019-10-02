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
from pathlib import Path

from packit.config import JobConfig, JobType, JobTriggerType

from packit_service.config import ServiceConfig
from packit_service.service.events import Event
from packit_service.worker.handler import JobHandler


def test_handler_cleanup(tmpdir):
    t = Path(tmpdir)
    t.joinpath("a").mkdir()
    t.joinpath("b").write_text("a")
    t.joinpath("c").symlink_to("b")
    t.joinpath("d").symlink_to("a", target_is_directory=True)
    t.joinpath("e").symlink_to("nope", target_is_directory=False)
    t.joinpath("f").symlink_to("nopez", target_is_directory=True)
    t.joinpath(".g").write_text("g")
    t.joinpath(".h").symlink_to(".g", target_is_directory=False)

    c = ServiceConfig()
    c.command_handler_work_dir = t
    jc = JobConfig(JobType.copr_build, [], JobTriggerType.pull_request, {})
    j = JobHandler(c, jc, Event(JobTriggerType.pull_request))

    j._clean_workplace()

    assert len(list(t.iterdir())) == 0
