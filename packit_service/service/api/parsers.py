# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from flask import request
from fastapi import Request
from flask_restx import reqparse
from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum, IntEnum

DEFAULT_PAGE = 1
DEFAULT_PER_PAGE = 10

class PerPageChoices(int, Enum):
    TWO = 2
    TEN = 10
    TWENTY = 20
    THIRTY = 30
    FORTY = 40
    FIFTY = 50

# pagination_arguments = reqparse.RequestParser()
# pagination_arguments.add_argument(
#     "page",
#     type=int,
#     required=False,
#     default=1,
#     help="Page number",
# )
# pagination_arguments.add_argument(
#     "per_page",
#     type=int,
#     required=False,
#     choices=[2, 10, 20, 30, 40, 50],
#     default=DEFAULT_PER_PAGE,
#     help="Results per page",
# )

class Pagination_Arguments(BaseModel):
    page: Optional[int] = Field(default=1, description="Page number")
    per_page: Optional[PerPageChoices] = Field(default=DEFAULT_PER_PAGE, description="Results per page")


def indices(pagination_arguments: Pagination_Arguments):
    """Return indices of first and last entry based on request arguments"""
    page = pagination_arguments.page
    per_page = pagination_arguments.per_page
    first = (page - 1) * per_page
    last = page * per_page
    return first, last
