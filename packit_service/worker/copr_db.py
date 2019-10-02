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

    def add_build(
        self,
        build_id: int,
        commit_sha: str,
        pr_id: int,
        repo_name: str,
        repo_namespace: str,
        ref: str,
        https_url: str,
    ):
        """
        Save copr build with commit information
        :param repo_name: github repository name
        :param repo_namespace: github repository namespace
        :param build_id: copr build id
        :param commit_sha: commit sha
        :param pr_id: PR id
        :param ref: PR ref
        :param https_url: upstream url of the repo
        :return:
        """
        build_info = {
            "commit_sha": commit_sha,
            "pr_id": pr_id,
            "repo_name": repo_name,
            "repo_namespace": repo_namespace,
            "ref": ref,
            "https_url": https_url,
        }
        self.db[build_id] = build_info
        logger.debug(f"Saving build ({build_id}) : {build_info}")

    def delete_build(self, build_id: int) -> bool:
        """
        Remove build from DB
        :param build_id: copr build id
        :return: bool
        """
        if build_id in self.db:
            del self.db[build_id]
            logger.debug(f"Build: {build_id} deleted!")
            return True
        else:
            logger.debug(f"Build: {build_id} does not exists!")
            return False

    def get_build(self, build_id: int) -> Optional[dict]:
        """
        Get build from DB
        :param build_id:
        :return:
        """
        build = self.db[build_id]
        if not build:
            return None
        else:
            return build
