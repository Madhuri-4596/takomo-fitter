"""Vercel entry point: the FastAPI app (app/server.py) as one Python function.
Every path is routed here (vercel.json), so the fitter page, its scripts, the models and the API work as locally."""
from app.server import app  # noqa: F401
