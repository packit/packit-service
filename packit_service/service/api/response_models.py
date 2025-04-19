from pydantic import BaseModel
from typing import List, Optional, Dict, Any


class BodhiUpdatesListResponse(BaseModel):
    result: Optional[List]


class BodhiUpdateItemResponse(BaseModel):
    update_dict: Dict[str, Any]

class BodhiUpdateGroupResponse(BaseModel):
    group_dict: Dict[str, Any]