FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies (minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN python -m pip install --upgrade pip --retries 5 --timeout 60 && \
    pip install --no-cache-dir --retries 5 --timeout 60 -r requirements.txt

# Copy wideq library
COPY wideq/ ./wideq/

# Copy application
COPY mqtt_publisher.py .

# Create non-root user
RUN useradd -m -u 1000 pytherma

# Create log directory and set ownership
RUN mkdir -p /app/logs && \
    chown -R pytherma:pytherma /app

# Switch to non-root user
USER pytherma

# Health check – python is PID 1, check /proc/1/cmdline
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "open('/proc/1/cmdline','rb').read().find(b'mqtt_publisher')>=0 or exit(1)"

# Run application (unbuffered output for logs)
CMD ["python", "-u", "mqtt_publisher.py"]
