# Combined Dockerfile for Streamlit + FastAPI with Nginx reverse proxy
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies including nginx and supervisor
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    nginx \
    supervisor \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install uv && uv pip install --system --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .
COPY api.py .
COPY core/ ./core/

# Copy nginx configuration
COPY nginx.conf /etc/nginx/nginx.conf

# Copy supervisor configuration
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Expose nginx port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl --fail http://localhost:8080/api/health || exit 1

# Start supervisor (manages nginx, streamlit, and fastapi)
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
