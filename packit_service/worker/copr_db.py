# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.
#
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

from typing import Optional

import logging

from persistentdict.dict_in_redis import PersistentDict

logger = logging.getLogger(__name__)


class CoprBuildDB:
    """
    This is DB wrapper for copr builds. Packit service submits copr build, save the build ID
    and listen on fedmsg for updates of the build. For this purpose we need to track PR info
    (commit_sha, ref, pr_id) with corresponding build_id.
    """

    def __init__(self):
        self.db = PersistentDict(hash_name="copr_build")

    def add_build(self, build_id, commit_sha, pr_id, ref):
        build_info = {"commit_sha": commit_sha, "pr_id": pr_id, "ref": ref}
        self.db[build_id] = build_info
        logger.debug(f"Saving build ({build_id}) : {build_info}")

    def delete_build(self, build_id) -> bool:
        """
        Remove build from DB
        :param build_id: build id of copr build
        :return: bool
        """
        if build_id in self.db:
            del self.db[build_id]
            logger.debug(f"Build: {build_id} deleted!")
            return True
        else:
            logger.debug(f"Build: {build_id} does not exists!")
            return False

    def get_build(self, build_id) -> Optional[dict]:
        build = self.db[build_id]
        if not build:
            return None
        else:
            return build
