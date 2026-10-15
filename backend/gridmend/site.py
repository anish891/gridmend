from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from gridmend.api import app as api_app


def create_site(ui_dir: Path) -> FastAPI:
    """One origin for deployment: the API under /api and the exported UI at /."""
    site = FastAPI(title="GridMend")
    site.mount("/api", api_app)
    site.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")
    return site
