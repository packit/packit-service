# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from packit_service.utils import only_once


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
