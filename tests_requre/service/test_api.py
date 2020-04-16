import pytest
# import pytest-flask

from packit_service.service.app import get_flask_application
from flask import url_for

@pytest.fixture
def app():
    app = get_flask_application()
    return app

# Check if the API is working
def test_api_health(client):
    response = client.get(url_for('api.healthz_health_check'))
    assert response.json == "We are healthy!"

# Test Copr Builds
def test_api_health(client):
    response = client.get(url_for('api.healthz_health_check'))
    assert response.json == "We are healthy!"
    