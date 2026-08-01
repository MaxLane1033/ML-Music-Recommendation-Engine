from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .database import Base, engine
from .routers import artists, search, vibes

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Vibe Recommendation Engine")

app.include_router(vibes.router)
app.include_router(search.router)
app.include_router(artists.router)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
