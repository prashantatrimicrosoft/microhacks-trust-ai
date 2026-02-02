"""
OpenTelemetry Tracing Configuration for RAG Application.

Sets up distributed tracing with Azure Application Insights to capture:
- Full RAG workflow (search → format → generate)
- Azure OpenAI API calls
- Azure AI Search queries
- FastAPI/Streamlit request spans

Environment Variables:
    APPLICATIONINSIGHTS_CONNECTION_STRING: Required for Azure App Insights export
    AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED: Set to "true" to capture prompts/completions
"""

import os
import logging
from functools import wraps
from typing import Optional, Callable, Any

logger = logging.getLogger(__name__)

# Global tracer instance
_tracer = None
_tracing_initialized = False


def setup_tracing(
    service_name: str = "rag-application",
    connection_string: Optional[str] = None
) -> bool:
    """
    Initialize OpenTelemetry tracing with Azure Application Insights.
    
    Args:
        service_name: Name to identify this service in traces
        connection_string: Application Insights connection string. 
                          Falls back to APPLICATIONINSIGHTS_CONNECTION_STRING env var.
    
    Returns:
        True if tracing was successfully initialized, False otherwise
    """
    global _tracer, _tracing_initialized
    
    if _tracing_initialized:
        logger.info("Tracing already initialized")
        return True
    
    # Get connection string from parameter or environment
    # Check both standard Azure name and azd-provisioned name
    conn_string = (
        connection_string or 
        os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "") or
        os.environ.get("AZURE_APPINSIGHTS_CONNECTION_STRING", "")
    )
    
    if not conn_string:
        logger.warning(
            "Application Insights connection string not set. "
            "Tracing will be disabled. Set APPLICATIONINSIGHTS_CONNECTION_STRING or "
            "AZURE_APPINSIGHTS_CONNECTION_STRING environment variable to enable tracing."
        )
        return False
    
    try:
        # Enable content recording for AI operations (prompts and completions)
        os.environ["AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED"] = "true"
        os.environ["AZURE_SDK_TRACING_IMPLEMENTATION"] = "opentelemetry"
        
        # Use azure-monitor-opentelemetry for simplified setup with automatic version compatibility
        from azure.monitor.opentelemetry import configure_azure_monitor
        from opentelemetry import trace
        
        # Configure Azure Monitor with the connection string
        configure_azure_monitor(
            connection_string=conn_string,
            enable_live_metrics=True,
        )
        
        # Get tracer for this application
        _tracer = trace.get_tracer(service_name, "1.0.0")
        
        # Instrument FastAPI
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            FastAPIInstrumentor().instrument()
            logger.info("FastAPI instrumented for tracing")
        except ImportError:
            logger.debug("FastAPI instrumentation not available")
        except Exception as e:
            logger.warning(f"Failed to instrument FastAPI: {e}")
        
        # Instrument HTTP requests
        try:
            from opentelemetry.instrumentation.requests import RequestsInstrumentor
            RequestsInstrumentor().instrument()
            logger.info("Requests library instrumented for tracing")
        except ImportError:
            logger.debug("Requests instrumentation not available")
        except Exception as e:
            logger.warning(f"Failed to instrument requests: {e}")
        except Exception as e:
            logger.warning(f"Failed to instrument requests: {e}")
        
        _tracing_initialized = True
        logger.info(f"✅ Tracing initialized successfully for service: {service_name}")
        logger.info(f"   Traces will be sent to Azure Application Insights")
        
        return True
        
    except ImportError as e:
        logger.warning(
            f"Tracing dependencies not installed: {e}. "
            "Install with: pip install azure-monitor-opentelemetry-exporter opentelemetry-sdk"
        )
        return False
    except Exception as e:
        logger.error(f"Failed to initialize tracing: {e}")
        return False


def get_tracer():
    """
    Get the configured tracer instance.
    
    Returns:
        OpenTelemetry tracer or None if tracing is not initialized
    """
    global _tracer
    
    if _tracer is None and not _tracing_initialized:
        # Try to get a tracer from the global provider
        try:
            from opentelemetry import trace
            _tracer = trace.get_tracer(__name__, "1.0.0")
        except Exception:
            pass
    
    return _tracer


def trace_span(
    name: str,
    attributes: Optional[dict] = None
):
    """
    Decorator to trace a function with a custom span.
    
    Args:
        name: Name of the span
        attributes: Optional attributes to add to the span
    
    Example:
        @trace_span("search_documents", {"search.type": "hybrid"})
        def search_documents(query: str):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            tracer = get_tracer()
            
            if tracer is None:
                # Tracing not available, just call the function
                return func(*args, **kwargs)
            
            with tracer.start_as_current_span(name) as span:
                # Add custom attributes
                if attributes:
                    for key, value in attributes.items():
                        span.set_attribute(key, value)
                
                try:
                    result = func(*args, **kwargs)
                    span.set_attribute("status", "success")
                    return result
                except Exception as e:
                    span.set_attribute("status", "error")
                    span.set_attribute("error.message", str(e))
                    span.record_exception(e)
                    raise
        
        return wrapper
    return decorator


def start_span(name: str, attributes: Optional[dict] = None):
    """
    Start a new span manually for more complex tracing scenarios.
    
    Args:
        name: Name of the span
        attributes: Optional attributes to add to the span
    
    Returns:
        Context manager for the span, or a no-op context if tracing is disabled
    
    Example:
        with start_span("rag_workflow", {"query": user_query}) as span:
            span.set_attribute("step", "search")
            documents = search(query)
            span.set_attribute("documents_found", len(documents))
    """
    tracer = get_tracer()
    
    if tracer is None:
        # Return a no-op context manager
        from contextlib import nullcontext
        return nullcontext()
    
    span = tracer.start_as_current_span(name)
    
    # We need to handle attributes after span starts
    if attributes:
        # Get the actual span object to set attributes
        from opentelemetry import trace
        current_span = trace.get_current_span()
        if current_span:
            for key, value in attributes.items():
                if value is not None:
                    current_span.set_attribute(key, value)
    
    return span


def add_span_attribute(key: str, value: Any) -> None:
    """
    Add an attribute to the current span.
    
    Args:
        key: Attribute name
        value: Attribute value
    """
    try:
        from opentelemetry import trace
        span = trace.get_current_span()
        if span and value is not None:
            # Convert to string if not a supported type
            if isinstance(value, (str, bool, int, float)):
                span.set_attribute(key, value)
            else:
                span.set_attribute(key, str(value))
    except Exception:
        pass  # Silently ignore if tracing is not available


def add_span_event(name: str, attributes: Optional[dict] = None) -> None:
    """
    Add an event to the current span.
    
    Args:
        name: Event name
        attributes: Optional event attributes
    """
    try:
        from opentelemetry import trace
        span = trace.get_current_span()
        if span:
            span.add_event(name, attributes=attributes or {})
    except Exception:
        pass  # Silently ignore if tracing is not available


def record_exception(exception: Exception) -> None:
    """
    Record an exception in the current span.
    
    Args:
        exception: The exception to record
    """
    try:
        from opentelemetry import trace
        span = trace.get_current_span()
        if span:
            span.record_exception(exception)
            span.set_attribute("error", True)
            span.set_attribute("error.message", str(exception))
    except Exception:
        pass  # Silently ignore if tracing is not available
