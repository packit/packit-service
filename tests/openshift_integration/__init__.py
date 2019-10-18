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

from requre.helpers.requests_response import RequestResponseHandling
from requre.import_system import upgrade_import_system

ogr_import_system = upgrade_import_system(
    filters=[
        ("^requests$", {"who_name": "ogr"}),
        ("^requests$", {"who_name": "gitlab"}),
        ("^requests$", {"who_name": "github"}),
    ],
    debug_file="modules.out",
).decorate(
    where="^requests$",
    what="Session.send",
    who_name=[
        "ogr.services.pagure",
        "gitlab",
        "github.MainClass",
        "github.Requester",
        "ogr.services.github_tweak",
    ],
    decorator=RequestResponseHandling.decorator(item_list=[]),
)
