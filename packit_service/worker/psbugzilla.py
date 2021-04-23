# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

# This module is called psbugzilla to avoid conflicts with python-bugzilla module.

import logging
from tempfile import TemporaryFile
from typing import List, Tuple
from xmlrpc.client import Fault

import backoff
from bugzilla import Bugzilla as XMLRPCBugzilla


class Bugzilla:
    """ To create a Bugzilla bug & attach a patch. Uses Bugzilla XMLRPC access module. """

    def __init__(self, url: str, api_key: str):
        self.logger = logging.getLogger(__name__)
        self.url = url
        self._api_key = api_key
        self._api = None

    @property
    def api(self):
        if self._api is None:
            self._api = XMLRPCBugzilla(url=self.url, api_key=self._api_key)
            try:
                if not self._api.logged_in:
                    raise ValueError("Empty or invalid Bugzilla api_key")
            except Fault as exc:
                raise ValueError(exc.faultString)
        return self._api

    def create_bug(
        self,
        product: str,
        version: str,
        component: str,
        summary: str,
        description: str = None,
        devel_whiteboard: str = None,
        keywords: List[str] = None,
    ) -> Tuple[int, str]:
        """
        Create a new bug.

        :param product: name of the product the bug is being filed against
        :param version: a version of the product above; the version the bug was found in
        :param component: name of a component in the product above
        :param summary: a brief description of the bug being filed
        :param description: initial description for this bug
        :param devel_whiteboard: additional info
        :param keywords: list of keywords
        :return: (ID, url) of the newly-filed bug
        """
        createinfo = self.api.build_createbug(
            product=product,
            version=version,
            component=component,
            summary=summary,
            description=description or "No description",
            keywords=keywords,
        )

        if devel_whiteboard:
            createinfo["cf_devel_whiteboard"] = devel_whiteboard

        self.logger.info(f"Creating a new bug for {component} in {product}:{version}")
        try:
            newbug = self.api.createbug(createinfo)
        except Fault as exc:
            createinfo.pop("Bugzilla_api_key", None)
            msg = f"Failed to create a bug with {createinfo}. Exception: {exc.faultString}"
            raise RuntimeError(msg)
        self.logger.info(f"Created bug #{newbug.id} at {newbug.weburl}")
        return newbug.id, newbug.weburl

    @backoff.on_exception(wait_gen=backoff.expo, exception=RuntimeError, max_time=30)
    def add_patch(self, bzid: int, content: bytes, file_name: str = None) -> int:
        """
        Add 'content' as attachment/patch into bug 'bzid'.

        :param bzid: Bugzilla bug to add patch to
        :param content: patch content
        :param file_name: attachment/patch file name
        :return: attachment id
        """
        with TemporaryFile() as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            tmp_file.seek(0)
            self.logger.info(f"Adding an attachment to bug #{bzid}")
            try:
                attachment_id = self.api.attachfile(
                    idlist=bzid,
                    attachfile=tmp_file,
                    description="Approved patch",
                    file_name=file_name or "patch",
                    is_patch=True,
                    content_type="text/plain",
                )
            except Fault as exc:
                # This might be a 'query serialization error' if the bug has just been created.
                # Hence the @backoff.on_exception to retry.
                msg = f"Failed to add an attachment to bug #{bzid}. Exception: {exc.faultString}"
                raise RuntimeError(msg)
            self.logger.info(f"Added attachment {attachment_id} to bug #{bzid}")
        return attachment_id


# PAGURE_TOKEN="ABC" BUGZILLA_API_KEY="XYZ" python3 psbugzilla.py
if __name__ == "__main__":
    from os import getenv
    from ogr.services.pagure import PagureService

    namespace = getenv("NAMESPACE") or "source-git"
    repo = getenv("REPO") or "rpm"
    pr_id = int(getenv("PR_ID")) or 19
    service = PagureService(
        instance_url="https://git.stg.centos.org",
        token=getenv("PAGURE_TOKEN"),
        read_only=True,
    )
    project = service.get_project(namespace=namespace, repo=repo)
    pr = project.get_pr(pr_id)

    bz = Bugzilla(
        url="https://partner-bugzilla.redhat.com", api_key=getenv("BUGZILLA_API_KEY")
    )
    logging.basicConfig()
    bz.logger.setLevel(logging.DEBUG)
    description = f"Based on approved CentOS Stream Pull Request: {pr.url}"
    bzid, url = bz.create_bug(
        product="Red Hat Enterprise Linux 8",
        version="CentOS-Stream",
        component=repo,
        summary=pr.title,
        description=description,
    )
    bz.add_patch(bzid=bzid, content=pr.patch, file_name=f"centos-pr-{pr_id}.patch")
