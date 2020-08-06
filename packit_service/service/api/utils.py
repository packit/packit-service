from flask import make_response
from http import HTTPStatus
from json import dumps


def response_maker(result, status=HTTPStatus.OK.value):
    """response_maker is a wrapper around flask's make_response"""
    resp = make_response(dumps(result), status)
    resp.headers["Content-Type"] = "application/json"
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp
