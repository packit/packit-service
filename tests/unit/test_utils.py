# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
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
