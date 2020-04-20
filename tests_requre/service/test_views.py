import pytest

from tests_requre.service.conftest import build_info_dict

from packit_service.models import get_sa_session, CoprBuildModel
from packit_service.service.app import get_flask_application

from flask import url_for


@pytest.fixture
def app():
    app = get_flask_application()
    return app


#  Test SRPM Logs view
def test_srpm_logs_view(client, clean_before_and_after, multiple_copr_builds):
    with get_sa_session() as session:
        build = CoprBuildModel.get_by_build_id(
            str(270520), build_info_dict["chroots"][0]
        )
        # Logs view uses the id of the SRPMBuildModel not CoprBuildModel
        response = client.get(
            url_for("builds.get_srpm_build_logs_by_id", id_=str(build.srpm_build.id))
        )
        assert (
            str(response.data) == f"b'<html><head><title>SRPM Build id="
            f"{build.srpm_build.id}</title></head>"
            f"<body>SRPM creation logs:<br><br><pre>Some boring logs.</pre><br></body></html>'"
        )


#  Test Build Logs view
def test_copr_build_logs_view(client, clean_before_and_after, multiple_copr_builds):
    with get_sa_session() as session:
        build = CoprBuildModel.get_by_build_id(
            str(270520), build_info_dict["chroots"][0]
        )
        # Logs view uses the id of the SRPMBuildModel not CoprBuildModel
        response = client.get(url_for("builds.get_build_logs_by_id", id_=str(build.id)))
        print(response.data)
        assert (
            str(response.data) == f"b'<html><head><title>Build marvel/aos: "
            f"PR #2705</title></head><body>COPR Build ID: 270520<br>"
            f"State: success<br><br>Build web interface URL: "
            f'<a href="https://copr.something.somewhere/270520">'
            f"https://copr.something.somewhere/270520</a>"
            f"<br>SRPM creation logs:<br><br><pre>"
            f"Some boring logs.</pre><br></body></html>'"
        )
