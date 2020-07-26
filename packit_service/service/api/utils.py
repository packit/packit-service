from flask import make_response
from http import HTTPStatus
from json import dumps


def response_maker(result, content_range=None, status=HTTPStatus.OK):
    """response_maker is a wrapper around flask's make_response"""
    resp = make_response(dumps(result), status)
    if content_range:
        resp.headers["Content-Range"] = content_range
    resp.headers["Content-Type"] = "application/json"
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp
