FROM python:3.12-slim

WORKDIR /agentOS

# System deps — core CLI tools pre-installed for the app catalog
RUN apt-get update && apt-get install -y --no-install-recommends \
    ripgrep \
    fd-find \
    fzf \
    jq \
    bat \
    curl \
    git \
    wget \
    unzip \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# bat is installed as batcat on Debian/Ubuntu — symlink to bat
RUN ln -sf /usr/bin/batcat /usr/local/bin/bat 2>/dev/null || true
# fd-find is installed as fdfind — symlink to fd
RUN ln -sf /usr/bin/fdfind /usr/local/bin/fd 2>/dev/null || true

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

# entrypoint.sh starts the autonomy daemon in the background, then uvicorn
RUN chmod +x entrypoint.sh
CMD ["./entrypoint.sh"]
