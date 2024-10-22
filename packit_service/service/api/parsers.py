# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from flask import request
from flask_restx import reqparse

DEFAULT_PAGE = 1
DEFAULT_PER_PAGE = 10

pagination_arguments = reqparse.RequestParser()
pagination_arguments.add_argument(
    "page",
    type=int,
    required=False,
    default=1,
    help="Page number",
)
pagination_arguments.add_argument(
    "per_page",
    type=int,
    required=False,
    choices=[2, 10, 20, 30, 40, 50],
    default=DEFAULT_PER_PAGE,
    help="Results per page",
)


def indices():
    """Return indices of first and last entry based on request arguments"""
    args = pagination_arguments.parse_args(request)
    page = args.get("page", DEFAULT_PAGE)
    if page < DEFAULT_PAGE:
        page = DEFAULT_PAGE
    per_page = args.get("per_page", DEFAULT_PER_PAGE)
    first = (page - 1) * per_page
    last = page * per_page
    return first, last
