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
from json import loads, dumps
from logging import getLogger
from os import getenv

from flask import make_response
from flask_restplus import Namespace, Resource
from packit.utils import nested_get
from redis import Redis

from packit_service.service.api.parsers import pagination_arguments, indices
from packit_service.service.events import Event

logger = getLogger("packit_service")

ns = Namespace("tasks", description="Celery tasks / jobs")

# Can't use PersistendDict
db = Redis(
    host=getenv("REDIS_SERVICE_HOST", "localhost"),
    port=int(getenv("REDIS_SERVICE_PORT", 6379)),
    db=0,
    decode_responses=True,
)


@ns.route("")
class TasksList(Resource):
    @ns.expect(pagination_arguments)
    @ns.response(HTTPStatus.PARTIAL_CONTENT, "Celery tasks list follows")
    def get(self):
        """ List all Celery tasks / jobs """
        first, last = indices()

        tasks = []
        # The db.keys() always returns all matched keys, but there's no better way with redis.
        # Use islice (instead of [first:last]) to at least create an iterator instead of new list.
        keys = db.keys("celery-task-meta-*")
        for key in islice(keys, first, last):
            data = db.get(key)
            if data:
                data = loads(data)
                event = nested_get(data, "result", "event")
                if event:  # timestamp to datetime string
                    data["result"]["event"] = Event.ts2str(data["result"]["event"])
                tasks.append(data)

        resp = make_response(dumps(tasks), HTTPStatus.PARTIAL_CONTENT)
        resp.headers["Content-Range"] = f"tasks {first+1}-{last}/{len(keys)}"
        resp.headers["Content-Type"] = "application/json"
        return resp


@ns.route("/<string:id>")
@ns.param("id", "Celery task identifier")
class TaskItem(Resource):
    @ns.response(HTTPStatus.OK, "OK, Celery task details follow")
    @ns.response(HTTPStatus.NO_CONTENT, "Celery task identifier not in db/hash")
    def get(self, id: str):
        """A specific Celery task details"""
        data = db.get(f"celery-task-meta-{id}")
        return loads(data) if data else ("", HTTPStatus.NO_CONTENT)
