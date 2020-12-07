# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
from packit_service.models import TestingFarmResult
from packit_service.service.events import TestResult
from packit_service.worker.parser import Parser

from tests.spellbook import DATA_DIR


def test_parse_tf_result_xunit():
    xunit_str = (
        (DATA_DIR / "webhooks" / "testing_farm" / "request_result_xunit.xml")
        .read_text()
        .replace("\t", "")
        .replace("\n", "")
    )
    results = [
        TestResult(
            name="/script-00",
            result=TestingFarmResult.passed,
            log_url="http://artifacts.dev.testing-farm.io/129bd474-e4d3-49e0-9dec-d994a99feebc/"
            "work-fullCbeHbS/ci/test/build/full/execute/data/script-00/out.log",
        ),
        TestResult(
            name="/script-00",
            result=TestingFarmResult.passed,
            log_url="http://artifacts.dev.testing-farm.io/129bd474-e4d3-49e0-9dec-d994a99feebc/"
            "work-session-recordingMT55GL/ci/test/build/"
            "session-recording/execute/data/script-00/out.log",
        ),
        TestResult(
            name="/script-00",
            result=TestingFarmResult.passed,
            log_url="http://artifacts.dev.testing-farm.io/129bd474-e4d3-49e0-9dec-d994a99feebc/"
            "work-smokevPCRWz/ci/test/build/smoke/execute/data/script-00/out.log",
        ),
        TestResult(
            name="/script-01",
            result=TestingFarmResult.passed,
            log_url="http://artifacts.dev.testing-farm.io/129bd474-e4d3-49e0-9dec-d994a99feebc/"
            "work-smokevPCRWz/ci/test/build/smoke/execute/data/script-01/out.log",
        ),
        TestResult(
            name="/script-02",
            result=TestingFarmResult.passed,
            log_url="http://artifacts.dev.testing-farm.io/129bd474-e4d3-49e0-9dec-d994a99feebc/"
            "work-smokevPCRWz/ci/test/build/smoke/execute/data/script-02/out.log",
        ),
        TestResult(
            name="/script-03",
            result=TestingFarmResult.passed,
            log_url="http://artifacts.dev.testing-farm.io/129bd474-e4d3-49e0-9dec-d994a99feebc/"
            "work-smokevPCRWz/ci/test/build/smoke/execute/data/script-03/out.log",
        ),
    ]
    assert Parser._parse_tf_result_xunit(xunit_str) == results
