import pytest
import json

from packit_service.worker.jobs import SteveJobs
from tests.spellbook import DATA_DIR


@pytest.fixture()
def installation():
    with open(DATA_DIR / "webhooks" / "installation.json", "r") as outfile:
        return json.load(outfile)


def test_github_app_installation(installation):
    steve = SteveJobs()
    github_app_response = steve.get_job_input_from_github_app_installation(installation)

    assert github_app_response
    trigger, github_app = github_app_response
    github_app.installation_id = 1173510
    github_app.account_login = "user-cont"
    github_app.account_id = 26160778
    github_app.account_url = "https://api.github.com/users/rpitonak"
    github_app.account_type = "User"
    github_app.created_at = 1560941425
    github_app.sender_id = 26160778
    github_app.sender_login = "rpitonak"
