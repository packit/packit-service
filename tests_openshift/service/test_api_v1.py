# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from fastapi.testclient import TestClient

from packit_service.service.app import app

client = TestClient(app)


def test_healthz():
    response = client.get("/v1/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_system():
    response = client.get("/v1/system")
    assert response.status_code == 200
    data = response.json()

    expected_keys = {"ogr", "specfile", "packit", "packit_service"}
    assert set(data.keys()) == expected_keys

    for key in expected_keys:
        assert isinstance(data[key], dict)
        assert "commit" in data[key]
        assert "version" in data[key]


def test_meta():
    """Test meta info like headers."""
    response = client.get("/v1/healthz")
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
