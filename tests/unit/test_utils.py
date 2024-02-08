# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT
import pytest
from flexmock import flexmock

from packit_service.utils import only_once, pr_labels_match_configuration


def test_only_once():
    counter = 0

    @only_once
    def f():
        nonlocal counter
        counter += 1

    assert counter == 0
    f()
    assert counter == 1
    f()
    assert counter == 1
    f()
    assert counter == 1


def test_only_once_with_args():
    counter = 0

    @only_once
    def f(one, two):
        nonlocal counter
        counter += 1
        assert one == two

    assert counter == 0
    f(1, 1)
    assert counter == 1
    f("a", "a")
    assert counter == 1
    f("b", "b")
    assert counter == 1


def test_only_once_with_kwargs():
    counter = 0

    @only_once
    def f(one, two):
        nonlocal counter
        counter += 1
        assert one == two

    assert counter == 0
    f(one=1, two=1)
    assert counter == 1
    f(one="a", two="a")
    assert counter == 1
    f(one="b", two="b")
    assert counter == 1


def test_only_once_with_args_and_kwargs():
    counter = 0

    @only_once
    def f(one, two, three="something"):
        nonlocal counter
        counter += 1
        assert one == two
        assert three

    assert counter == 0
    f(1, 1, three="different")
    assert counter == 1
    f("a", "a", three="different")
    assert counter == 1
    f("b", "b", three="different")
    assert counter == 1


@pytest.mark.parametrize(
    "absent,present,pr_labels,should_pass",
    [
        pytest.param(
            [],
            ["my-label"],
            [],
            False,
        ),
        pytest.param(
            [],
            ["my-label"],
            [flexmock(name="my-label")],
            True,
        ),
        pytest.param(
            ["skip-ci"],
            ["my-label"],
            [flexmock(name="my-label")],
            True,
        ),
        pytest.param(
            ["skip-ci"],
            ["my-label"],
            [flexmock(name="my-label"), flexmock(name="skip-ci")],
            False,
        ),
        pytest.param(
            ["skip-ci"],
            ["my-label"],
            [flexmock(name="skip-ci")],
            False,
        ),
        pytest.param(
            ["skip-ci"],
            [],
            [flexmock(name="skip-ci")],
            False,
        ),
        pytest.param(
            ["skip-ci"],
            [],
            [],
            True,
        ),
        pytest.param(
            ["skip-ci"],
            ["first", "second"],
            [flexmock(name="second")],
            True,
        ),
        pytest.param(
            ["skip-ci"],
            ["first", "second"],
            [flexmock(name="third")],
            False,
        ),
        pytest.param(
            ["skip-ci", "block-ci"],
            ["first", "second"],
            [flexmock(name="block-ci")],
            False,
        ),
        pytest.param(
            [],
            [],
            [],
            True,
        ),
        pytest.param(
            [],
            [],
            [flexmock(name="some-label")],
            True,
        ),
    ],
)
def test_pr_labels_match(absent, present, pr_labels, should_pass):
    assert (
        pr_labels_match_configuration(flexmock(labels=pr_labels, id=1), present, absent)
        == should_pass
    )
