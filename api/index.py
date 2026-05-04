"""Vercel serverless entry — re-exports the FastAPI app."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from web.app import app  # noqa: F401  — Vercel picks up `app`
