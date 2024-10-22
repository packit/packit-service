# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import os
import unittest
from glob import glob
from pathlib import Path
from shutil import copy

from packit.config import RunCommandType
from requre.cassette import StorageMode
from requre.constants import RELATIVE_TEST_DATA_DIRECTORY

from packit_service.worker.jobs import SteveJobs

PROJECT_DIR = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_DIR / "tests" / "data"


class PackitServiceTestCase(unittest.TestCase):
    def setUp(self):
        self._steve = None

    @property
    def steve(self):
        if not self._steve:
            self._steve = SteveJobs()
            self._steve.service_config.command_handler = RunCommandType.local
            self._steve.service_config.command_handler_work_dir = "/tmp/hello-world"
        return self._steve

    def cassette_teardown(self, cassette):
        # copy files to destination, where is mounted persistent volume
        cassette.dump()
        if cassette.mode == StorageMode.write:
            destdir = (
                Path("/tmp")
                / Path(RELATIVE_TEST_DATA_DIRECTORY)
                / Path(cassette.storage_file).parent.name
            )
            os.makedirs(destdir, exist_ok=True)
            storage_file = Path(cassette.storage_file)
            for filename in glob(f"{storage_file}*"):
                copy(filename, destdir)
