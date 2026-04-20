"""Embed widget routes (JS SDK + iframe)."""
import json
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response as FResponse, FileResponse

from war_room.dashboard.routes.deps import SEMECLAW_PUBLIC_URL

router = APIRouter(tags=["embed"])


@router.get("/embed.js")
async def api_embed_js():
    """Tiny JS SDK ??? drop-in <script> that mounts the War Room in any page.

    Usage:
        <script src="https://semeclaw.example.com/embed.js"></script>
        <div data-semeclaw-meeting="ops-review.md"
             data-semeclaw-v="2"
             style="width:100%;height:640px"></div>
    """
    base = SEMECLAW_PUBLIC_URL
    js = f"""(function() {{
  var BASE = {json.dumps(base)};
  function mount(el) {{
    if (el.getAttribute("data-semeclaw-mounted") === "1") return;
    el.setAttribute("data-semeclaw-mounted", "1");
    var meeting = el.getAttribute("data-semeclaw-meeting") || "";
    var layout  = el.getAttribute("data-semeclaw-v") || "1";
    var theme   = el.getAttribute("data-semeclaw-theme") || "dark";
    var url = BASE + "/embed?v=" + encodeURIComponent(layout) +
              "&theme=" + encodeURIComponent(theme) +
              (meeting ? "&meeting=" + encodeURIComponent(meeting) : "");
    var iframe = document.createElement("iframe");
    iframe.src = url;
    iframe.style.width = el.style.width || "100%";
    iframe.style.height = el.style.height || "640px";
    iframe.style.border = "0";
    iframe.style.borderRadius = el.style.borderRadius || "12px";
    iframe.setAttribute("allow", "autoplay; clipboard-write");
    iframe.setAttribute("loading", "lazy");
    iframe.title = "SemeClaw War Room";
    el.innerHTML = "";
    el.appendChild(iframe);
  }}
  function scan() {{
    var nodes = document.querySelectorAll("[data-semeclaw-meeting], [data-semeclaw-embed]");
    for (var i = 0; i < nodes.length; i++) mount(nodes[i]);
  }}
  if (document.readyState === "loading") {{
    document.addEventListener("DOMContentLoaded", scan);
  }} else {{
    scan();
  }}
  window.SemeClaw = {{ mount: mount, scan: scan, base: BASE }};
}})();
"""
    return FResponse(
        content=js,
        media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/embed/manifest.json")
async def api_embed_manifest():
    return JSONResponse({
        "widget": "semeclaw-war-room",
        "script_url": f"{SEMECLAW_PUBLIC_URL}/embed.js",
        "iframe_url": f"{SEMECLAW_PUBLIC_URL}/embed",
        "min_width":  320,
        "min_height": 420,
        "attributes": [
            {"name": "data-semeclaw-meeting", "required": False, "desc": "Report filename to play"},
            {"name": "data-semeclaw-v",       "required": False, "desc": "Layout version: 1 | 2 (orbital)"},
            {"name": "data-semeclaw-theme",   "required": False, "desc": "dark | light (dark only for now)"},
        ],
    })


@router.get("/embed")
async def embed_page(meeting: str = "", v: str = "1", theme: str = "dark"):
    """Serve the dashboard HTML with query-param hints for embed consumers.
    The main index.html reads window.location.search to auto-open a meeting."""
    index = Path(__file__).parent.parent / "index.html"
    if not index.exists():
        return JSONResponse({"error": "index not found"}, status_code=500)
    return FileResponse(index, media_type="text/html",
                        headers={"X-SemeClaw-Embed": "1",
                                 "X-SemeClaw-Meeting": meeting or "",
                                 "X-SemeClaw-Layout": v})
