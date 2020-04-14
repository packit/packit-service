# MIT License
#
# Copyright (c) 2019 Red Hat, Inc.
#
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
from http import HTTPStatus
from itertools import islice
from json import dumps
from logging import getLogger

from flask import make_response

try:
    from flask_restx import Namespace, Resource
except ModuleNotFoundError:
    from flask_restplus import Namespace, Resource

from packit_service.models import TaskResultModel
from packit_service.service.api.parsers import pagination_arguments, indices
from packit_service.service.events import Event

logger = getLogger("packit_service")

ns = Namespace("tasks", description="Celery tasks / jobs")


@ns.route("")
class TasksList(Resource):
    @ns.expect(pagination_arguments)
    @ns.response(HTTPStatus.PARTIAL_CONTENT, "Celery tasks list follows")
    def get(self):
        """ List all Celery tasks / jobs """
        first, last = indices()
        tasks = []
        for task in islice(TaskResultModel.get_all(), first, last):
            data = task.to_dict()
            data["event"] = Event.ts2str(data["event"])
            tasks.append(data)

        resp = make_response(dumps(tasks), HTTPStatus.PARTIAL_CONTENT)
        resp.headers["Content-Range"] = f"tasks {first+1}-{last}/{len(tasks)}"
        resp.headers["Content-Type"] = "application/json"
        return resp


@ns.route("/<string:id>")
@ns.param("id", "Celery task identifier")
class TaskItem(Resource):
    @ns.response(HTTPStatus.OK, "OK, Celery task details follow")
    @ns.response(HTTPStatus.NO_CONTENT, "Celery task identifier not in db")
    def get(self, id: str):
        """A specific Celery task details"""
        task = TaskResultModel.get_by_id(id)

        if not task:
            return "", HTTPStatus.NO_CONTENT

        data = task.to_dict()
        data["event"] = Event.ts2str(data["event"])
        return data
