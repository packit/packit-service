# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from packit.vm_image_build import ImageBuilder
from packit_service.worker.mixin import (
    GetVMImageBuilderMixin,
    ConfigFromEventMixin,
    GetVMImageDataMixin,
)


def test_GetVMImageBuilderMixin():
    class Test(ConfigFromEventMixin, GetVMImageBuilderMixin):
        ...

    mixin = Test()
    assert isinstance(mixin.vm_image_builder, ImageBuilder)


def test_GetVMImageDataMixin(fake_package_config_job_config_project_db_trigger):
    class Test(ConfigFromEventMixin, GetVMImageDataMixin):
        def __init__(self) -> None:
            super().__init__()
            (
                package_config,
                job_config,
                project,
                _,
            ) = fake_package_config_job_config_project_db_trigger
            self.package_config = package_config
            self.job_config = job_config
            self._project = project

    mixin = Test()
    assert mixin.chroot == "fedora-36-x86_64"
    assert mixin.identifier == ""
    assert mixin.owner == "mmassari"
    assert mixin.project_name == "knx-stack"
    assert mixin.image_distribution == "fedora-36"
    assert mixin.image_request == {
        "architecture": "x86_64",
        "image_type": "aws",
        "upload_request": {"type": "aws", "options": {}},
    }
    assert mixin.image_customizations == {"packages": ["python-knx-stack"]}
