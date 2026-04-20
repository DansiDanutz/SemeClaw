"""Alert and morning brief routes."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["alerts"])
