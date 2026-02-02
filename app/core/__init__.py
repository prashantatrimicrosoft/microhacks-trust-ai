"""
Core shared modules for RAG application.

This package contains shared functionality used by both the Streamlit UI (app.py)
and FastAPI service (api.py).
"""

from .config import Settings, get_settings
from .rag import RAGService, RAGResponse
from .tracing import setup_tracing, get_tracer, add_span_attribute, add_span_event

__all__ = [
    "Settings", 
    "get_settings", 
    "RAGService", 
    "RAGResponse",
    "setup_tracing",
    "get_tracer",
    "add_span_attribute",
    "add_span_event"
]
