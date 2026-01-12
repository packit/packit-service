# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
async def health_check() -> dict:
    """Health check"""
    return {"status": "ok"}
