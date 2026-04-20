"""Billing and tenant cost routes."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["billing"])
