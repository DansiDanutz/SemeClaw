"""Agent definition and manifest routes."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["agents"])
