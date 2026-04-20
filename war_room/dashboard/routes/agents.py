"""Agent definition and manifest routes."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["agents"])

    return JSONResponse({
        "id": "semeclaw-war-room",
        "name": "SemeClaw War Room",
        "version": "0.7.0",
        "capabilities": ["meeting.script", "meeting.audio", "reports.list"],
    })
