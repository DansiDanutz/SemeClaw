"""Meeting orchestration routes."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, FileResponse

from war_room.dashboard.routes.deps import ROOT
from war_room.dashboard.r2_client import generate_presigned_url

router = APIRouter(tags=["meetings"])


@router.get("/api/ad/audio")
async def api_ad_audio(request: Request):
    """Redirect to the presigned R2 URL for the default ad MP3.

    Falls back to the local placeholder if R2 is not configured.
    """
    r2_key = request.query_params.get("key", "ads/nervix-default.mp3")
    presigned = generate_presigned_url(r2_key, expiration=300)
    if presigned:
        return RedirectResponse(presigned)
    # Fallback to local placeholder
    local_path = ROOT / "data" / "ads" / "nervix-default.mp3"
    if local_path.exists():
        return FileResponse(local_path, media_type="audio/mpeg")
    return JSONResponse({"error": "Ad audio not available"}, status_code=404)
