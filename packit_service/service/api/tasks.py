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
from json import loads
from logging import getLogger
from os import getenv

from flask_restplus import Namespace, Resource
from redis import Redis

logger = getLogger("packit_service")

ns = Namespace("tasks", description="Celery tasks / jobs")

# Can't use PersistendDict
db = Redis(
    host=getenv("REDIS_SERVICE_HOST", "localhost"),
    port=int(getenv("REDIS_SERVICE_PORT", 6379)),
    db=0,
    decode_responses=True,
)

# TODO:
#  pagination


@ns.route("/")
class TasksList(Resource):
    @ns.response(HTTPStatus.OK, "OK, Celery tasks list follows")
    def get(self):
        """ List all Celery tasks / jobs """
        tasks = []
        for key in db.keys("celery-task-meta-*"):
            data = db.get(key)
            if data:
                tasks.append(loads(data))
        return tasks


@ns.route("/<string:id>")
@ns.param("id", "Celery task identifier")
class TaskItem(Resource):
    @ns.response(HTTPStatus.OK, "OK, Celery task details follow")
    @ns.response(HTTPStatus.NO_CONTENT, "Celery task identifier not in db/hash")
    def get(self, id: str):
        """A specific Celery task details"""
        data = db.get(f"celery-task-meta-{id}")
        return loads(data) if data else ("", HTTPStatus.NO_CONTENT)
