import os
import unittest
import inspect
from pathlib import Path
from requre.storage import PersistentObjectStorage
from packit.config import RunCommandType
from packit_service.worker.jobs import SteveJobs

TEST_DATA_DIR = "test_data"
PROJECT_DIR = Path(__file__).parent.parent.parent
PERSISTENT_DATA_PREFIX = Path(__file__).parent.parent / TEST_DATA_DIR
DATA_DIR = PROJECT_DIR / "tests" / "data"


class PackitServiceTestCase(unittest.TestCase):
    def get_datafile_filename(
        self, path_prefix: Path = PERSISTENT_DATA_PREFIX, suffix="yaml"
    ) -> Path:
        test_file_name = os.path.basename(inspect.getfile(self.__class__)).rsplit(
            ".", 1
        )[0]
        test_class_name = f"{self.id()}.{suffix}"
        testdata_dirname = path_prefix / str(test_file_name)
        testdata_dirname.mkdir(mode=0o777, exist_ok=True)
        return testdata_dirname / test_class_name

    def setUp(self) -> None:
        if self.get_datafile_filename().exists():
            # if already exists, do not regenerate test file, what is stored inside tests dir
            PersistentObjectStorage().storage_file = str(self.get_datafile_filename())
        else:
            # store them to path where Persistent volume is mounted
            PersistentObjectStorage().storage_file = str(
                self.get_datafile_filename(path_prefix=Path("/tmp") / TEST_DATA_DIR)
            )
        self.steve = SteveJobs()
        self.steve.config.command_handler = RunCommandType.local
        self.steve.config.command_handler_work_dir = "/tmp/hello-world"

    def tearDown(self) -> None:
        PersistentObjectStorage().dump()
