"""FastAPI router for the AI Ratios module: dashboard + JSON API."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import cache

SLUG = "ai-ratios"
_HERE = Path(__file__).resolve().parent

router = APIRouter()
templates = Jinja2Templates(directory=str(_HERE / "templates"))


@router.get("/", include_in_schema=False)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "dashboard.html", {"api_base": f"/{SLUG}/api", **cache.get()}
    )


@router.get("/api/data")
def api_data():
    return cache.get()


@router.post("/api/refresh")
def api_refresh():
    try:
        return cache.refresh()
    except cache.Busy:
        raise HTTPException(status_code=409, detail="refresh already in progress")
