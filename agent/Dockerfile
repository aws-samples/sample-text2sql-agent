# Single-stage Dockerfile for ARM64 AgentCore Runtime deployment
FROM --platform=linux/arm64 python:3.13-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential gcc ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create non-root user
RUN groupadd -r agentcore && useradd -r -g agentcore -m agentcore

WORKDIR /app

# Install Python dependencies (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY --chown=agentcore:agentcore . .

# Remove unnecessary files
RUN rm -rf __pycache__ *.pyc .git .gitignore *.md tests/ .pytest_cache/ requirements.txt

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# OTEL auto-instrumentation
ENV AGENT_OBSERVABILITY_ENABLED=true
ENV OTEL_PYTHON_DISTRO=aws_distro
ENV OTEL_PYTHON_CONFIGURATOR=aws_configurator
ENV OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
ENV OTEL_TRACES_EXPORTER=otlp
ENV OTEL_RESOURCE_ATTRIBUTES=service.name=dwh-agent

USER agentcore
EXPOSE 8080

CMD ["opentelemetry-instrument", "python", "agent.py"]
