FROM python:3.12-slim

WORKDIR /agentOS

# System deps for ripgrep (used by semantic search) and curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    ripgrep \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . .

# Memory and workspace directories
RUN mkdir -p /agentOS/memory /agentOS/workspace/agents

# Config: use config.json if present, otherwise config.example.json
RUN test -f config.json || cp config.example.json config.json

EXPOSE 7777

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:7777/health || exit 1

CMD ["python3", "-m", "uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "7777", "--log-level", "warning"]
