# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import sandcastle


def test_get_api_client():
    """let's make sure we can get k8s API client"""
    assert sandcastle.Sandcastle.get_api_client()
