"""Embed widget routes (JS SDK + iframe)."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response as FResponse

router = APIRouter(tags=["embed"])

    js = "(function(){console.log('SemeClaw embed loaded');})();"
    return FResponse(content=js, media_type="application/javascript")

    return JSONResponse({"meeting": meeting, "version": v, "theme": theme})
