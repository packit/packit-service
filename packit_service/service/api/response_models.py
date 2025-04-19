# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class BodhiUpdatesListResponse(BaseModel):
    result: Optional[List]


class BodhiUpdateItemResponse(BaseModel):
    update_dict: Dict[str, Any]


class BodhiUpdateGroupResponse(BaseModel):
    group_dict: Dict[str, Any]
