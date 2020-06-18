import os
from shutil import copy
from pathlib import Path
from requre import RequreTestCase
from requre.constants import RELATIVE_TEST_DATA_DIRECTORY
from requre.cassette import StorageMode
from packit.config import RunCommandType
from packit_service.worker.jobs import SteveJobs

PROJECT_DIR = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_DIR / "tests" / "data"


class PackitServiceTestCase(RequreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.steve = SteveJobs()
        self.steve.config.command_handler = RunCommandType.local
        self.steve.config.command_handler_work_dir = "/tmp/hello-world"

    def tearDown(self):
        super().tearDown()
        # copy files to destination, where is mounted persistent volume
        if self.cassette.mode == StorageMode.write:
            destdir = (
                Path("/tmp")
                / Path(RELATIVE_TEST_DATA_DIRECTORY)
                / Path(self.cassette.storage_file).parent.name
            )
            os.makedirs(destdir, exist_ok=True)
            copy(
                self.cassette.storage_file,
                destdir / Path(self.cassette.storage_file).name,
            )
