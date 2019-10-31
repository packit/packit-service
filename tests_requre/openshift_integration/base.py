import os
import unittest
import inspect
from pathlib import Path
from requre.utils import STORAGE
from packit.config import RunCommandType
from packit_service.worker.jobs import SteveJobs

PROJECT_DIR = Path(__file__).parent.parent.parent
PERSISTENT_DATA_PREFIX = os.path.join(
    os.path.dirname(os.path.realpath(__file__)), "test_data"
)
DATA_DIR = PROJECT_DIR / "tests" / "data"


class PackitServiceTestCase(unittest.TestCase):
    def get_datafile_filename(self, path_prefix=PERSISTENT_DATA_PREFIX, suffix="yaml"):
        test_file_name = os.path.basename(inspect.getfile(self.__class__)).rsplit(
            ".", 1
        )[0]
        test_class_name = f"{self.id()}.{suffix}"
        testdata_dirname = os.path.join(path_prefix, test_file_name)
        os.makedirs(testdata_dirname, mode=0o777, exist_ok=True)
        return os.path.join(testdata_dirname, test_class_name)

    def setUp(self) -> None:
        STORAGE.storage_file = self.get_datafile_filename(path_prefix="/tmp/test_data")
        self.steve = SteveJobs()
        self.steve.config.command_handler = RunCommandType.local
        self.steve.config.command_handler_work_dir = "/tmp/hello-world"

    def tearDown(self) -> None:
        STORAGE.dump()
