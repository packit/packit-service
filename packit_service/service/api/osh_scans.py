# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from http import HTTPStatus
from logging import getLogger

from flask_restx import Namespace, Resource

from packit_service.models import (
    OSHScanModel,
    optional_timestamp,
)
from packit_service.service.api.parsers import indices, pagination_arguments
from packit_service.service.api.utils import get_project_info_from_build, response_maker

logger = getLogger("packit_service")

ns = Namespace("openscanhub-scans", description="OpenScanHub scans")


@ns.route("")
class ScansList(Resource):
    @ns.expect(pagination_arguments)
    @ns.response(HTTPStatus.PARTIAL_CONTENT.value, "Scans list follows")
    def get(self):
        """List all scans."""

        first, last = indices()
        result = []

        for scan in OSHScanModel.get_range(first, last):
            scan_dict = get_scan_info(scan)
            scan_dict["packit_id"] = scan.id
            result.append(scan_dict)

        resp = response_maker(
            result,
            status=HTTPStatus.PARTIAL_CONTENT,
        )
        resp.headers["Content-Range"] = f"openscanhub-scans {first + 1}-{last}/*"
        return resp


@ns.route("/<int:id>")
@ns.param("id", "Packit id of the scan")
class ScanItem(Resource):
    @ns.response(HTTPStatus.OK.value, "OK, scan details follow")
    @ns.response(HTTPStatus.NOT_FOUND.value, "Scan identifier not in db/hash")
    def get(self, id):
        """A specific copr build details for one chroot."""
        scan = OSHScanModel.get_by_id(int(id))
        if not scan:
            return response_maker(
                {"error": "No info about scan stored in DB"},
                status=HTTPStatus.NOT_FOUND,
            )

        return response_maker(get_scan_info(scan))


def get_scan_info(scan: OSHScanModel) -> dict:
    scan_dict = {
        "task_id": scan.task_id,
        "status": scan.status,
        "url": scan.url,
        "issues_added_count": scan.issues_added_count,
        "issues_added_url": scan.issues_added_url,
        "issues_fixed_url": scan.issues_fixed_url,
        "scan_results_url": scan.scan_results_url,
        "copr_build_target_id": scan.copr_build_target_id,
        "submitted_time": optional_timestamp(scan.submitted_time),
    }
    if scan.copr_build_target:
        scan_dict.update(get_project_info_from_build(scan.copr_build_target))
    return scan_dict
