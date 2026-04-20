"""Health, TTS, and board routes."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])

    return JSONResponse({"status": "ok", "version": "0.7.0"})
