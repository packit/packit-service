# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import shutil
import tempfile
from pathlib import Path

from packit.pkgtool import PkgTool
from requre.helpers.files import StoreFiles
from requre.helpers.git.fetchinfo import FetchInfoStorageList
from requre.helpers.git.pushinfo import PushInfoStorageList
from requre.helpers.git.repo import Repo
from requre.helpers.simple_object import Simple
from requre.helpers.tempfile import MkDTemp, MkTemp
from requre.online_replacing import (
    apply_decorator_to_all_methods,
    record_requests_for_all_methods,
    replace_module_match,
)

from tests_openshift.openshift_integration.base import PackitServiceTestCase

#        where="download_helper",
#        what="DownloadHelper.request",
#        who_name="lookaside_cache_helper",
#        decorator=RequestResponseHandling.decorator_plain(),


@record_requests_for_all_methods()
@apply_decorator_to_all_methods(
    replace_module_match(
        what="packit.utils.run_command_remote",
        decorate=Simple.decorator_plain(),
    ),
)
@apply_decorator_to_all_methods(
    replace_module_match(
        what="packit.pkgtool.PkgTool.clone",
        decorate=StoreFiles.where_arg_references(
            key_position_params_dict={"target_path": 2},
        ),
    ),
)
@apply_decorator_to_all_methods(
    replace_module_match(
        what="git.repo.base.Repo.clone_from",
        decorate=StoreFiles.where_arg_references(
            key_position_params_dict={"to_path": 2},
            output_cls=Repo,
        ),
    ),
)
@apply_decorator_to_all_methods(
    replace_module_match(
        what="git.remote.Remote.push",
        decorate=PushInfoStorageList.decorator_plain(),
    ),
)
@apply_decorator_to_all_methods(
    replace_module_match(
        what="git.remote.Remote.fetch",
        decorate=FetchInfoStorageList.decorator_plain(),
    ),
)
@apply_decorator_to_all_methods(
    replace_module_match(
        what="git.remote.Remote.pull",
        decorate=FetchInfoStorageList.decorator_plain(),
    ),
)
@apply_decorator_to_all_methods(
    replace_module_match(what="tempfile.mkdtemp", decorate=MkDTemp.decorator_plain()),
)
@apply_decorator_to_all_methods(
    replace_module_match(what="tempfile.mktemp", decorate=MkTemp.decorator_plain()),
)
# Be aware that decorator stores login and token to test_data, replace it by some value.
# Default precommit hook doesn't do that for copr.v3.helpers, see README.md
@apply_decorator_to_all_methods(
    replace_module_match(
        what="copr.v3.helpers.config_from_file",
        decorate=Simple.decorator_plain(),
    ),
)
class Pkgtool(PackitServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmpdir = None

    @property
    def tmpdir(self):
        if not self._tmpdir:
            self._tmpdir = tempfile.mkdtemp()
        return self._tmpdir

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir)
        super().tearDown()

    def test_pkgtool_clone(self):
        """test `fedpkg clone -a` within an openshift pod"""
        t = Path(self.tmpdir)
        PkgTool().clone("units", str(t), anonymous=True)
        assert t.joinpath("units.spec").is_file()
