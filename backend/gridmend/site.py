from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from gridmend.api import app as api_app
from gridmend.api import load_caches


@asynccontextmanager
async def lifespan(site: FastAPI):
    # Starlette does not run a mounted app's startup, so the site does it for the API.
    load_caches()
    yield


def create_site(ui_dir: Path) -> FastAPI:
    """One origin for deployment: the API under /api and the exported UI at /."""
    site = FastAPI(title="GridMend", lifespan=lifespan)
    site.mount("/api", api_app)
    site.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")
    return site
