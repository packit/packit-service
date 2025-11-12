# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import json

import pytest

from packit_service.events.logdetective import Result as LogDetectiveResultEvent
from packit_service.models import LogDetectiveBuildSystem, LogDetectiveResult
from packit_service.worker.parser import Parser


def test_logdetective_result_event_type():
    """Test event type return value"""

    assert LogDetectiveResultEvent.event_type() == "logdetective.result"


@pytest.mark.parametrize("result", [e.value for e in LogDetectiveResult])
@pytest.mark.parametrize("build_system", [e.value for e in LogDetectiveBuildSystem])
def test_parse_logdetective_analysis_result(
    logdetective_analysis_result, log_detective_result_event_creation, build_system, result
):
    """Test standard message from Log Detective with all combinations of build systems
    and result states"""

    logdetective_analysis_result["build_system"] = build_system
    logdetective_analysis_result["result"] = result

    event_object = Parser.parse_event(logdetective_analysis_result)

    assert isinstance(event_object, LogDetectiveResultEvent)
    assert isinstance(event_object.log_detective_response, dict)
    assert event_object.target_build == "9999"
    assert event_object.status == result
    assert event_object.build_system == LogDetectiveBuildSystem(build_system)

    # Attempt to serialize dictionary form of the object
    object_dict = event_object.get_dict()
    json.dumps(object_dict)


@pytest.mark.parametrize("build_system", [e.value for e in LogDetectiveBuildSystem])
def test_parse_logdetective_analysis_result_error(
    logdetective_analysis_result_error, log_detective_result_event_creation, build_system
):
    """When analysis returns `error` result, the `log_detective_response`
    is left empty."""

    logdetective_analysis_result_error["build_system"] = build_system
    event_object = Parser.parse_event(logdetective_analysis_result_error)

    assert isinstance(event_object, LogDetectiveResultEvent)
    assert event_object.log_detective_response is None
    assert event_object.target_build == "9999"
    assert event_object.status == "error"
    assert event_object.build_system == LogDetectiveBuildSystem(build_system)

    # Attempt to serialize dictionary form of the object
    object_dict = event_object.get_dict()
    json.dumps(object_dict)


def test_parse_logdetective_analysis_result_wrong_build_system(logdetective_analysis_result):
    """Test that results from unsupported build systems are discarded"""

    logdetective_analysis_result["build_system"] = "unsupported_build_system"
    event_object = Parser.parse_event(logdetective_analysis_result)

    assert event_object is None
