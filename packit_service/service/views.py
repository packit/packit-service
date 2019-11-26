"""
Flask views for packit-service
"""
from persistentdict.dict_in_redis import PersistentDict

from packit_service.service.app import application


@application.route("/build/<int:build_id>/<str:target>/logs", methods=("GET",))
def get_build_logs(build_id, target):
    db = PersistentDict(hash_name="copr_build")
    build = db.get(build_id)
    if build:
        try:
            build_target = build["targets"][target]
        except KeyError:
            return 404
        build_state = build_target["state"]
        build_logs = build_target["build_logs"]
        root_logs = build_target["root_logs"]
        web_url = build["web_url"]
        response = (
            f"Build {build_id} is in state {build_state}\n\n"
            + f"Build web interface URL: {web_url}\n"
            + f"Dependency installation logs: {root_logs}\n"
            + f"RPM build logs: {build_logs}\n\n"
            + "SRPM creation logs:\n"
            + build.get("logs", "No logs.")
        )
        return response
    return f"Build {build_id} does not exist.\n"
