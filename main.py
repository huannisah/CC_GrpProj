"""
main.py

Entry point for CareerAI-as-a-Service (CaaS).
Run locally with: uvicorn main:app --reload --port 8000
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from config import Config
from utils.analytics_service import init_db
from api.routes import web_routes, api_routes
import os

app = FastAPI(
    title="CareerAI as a Service (CaaS)",
    description=(
        "A cloud-native, API-first career intelligence platform. "
        "Each capability — ATS scoring, resume optimisation, cover letter generation, "
        "and job analysis — is exposed as an independent, stateless REST microservice. "
        "Every submission anonymously contributes to the public Job Market Intelligence API."
    ),
    version="1.0.0",
    contact={"name": "CareerAI CaaS"},
    license_info={"name": "MIT"},
)

# Session middleware — required for flash messages and temp file paths in the web UI
app.add_middleware(SessionMiddleware, secret_key=Config.SECRET_KEY)

# Mount static files (CSS, images, JS)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Ensure upload folder exists
os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)

# Initialise Databricks tables (wrapped in try/except — app works without it)
init_db()

# Register routers
# Web UI routes (HTML pages) — no prefix, served at /
app.include_router(web_routes.router, tags=["Web UI"])
# REST API routes — prefixed with /api/v1
app.include_router(api_routes.router, prefix="/api/v1", tags=["REST API v1"])
