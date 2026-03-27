#!/bin/bash
# AgentOS — Ollama + RTX 5070 Setup
# Installs Ollama, configures GPU, pulls recommended models
set -e

echo '{"step":"start","message":"Installing Ollama for RTX 5070"}'

# Install Ollama
if ! command -v ollama &>/dev/null; then
    curl -fsSL https://ollama.com/install.sh | sh
    echo '{"step":"ollama_installed","ok":true}'
else
    echo '{"step":"ollama_exists","ok":true}'
fi

# Configure Ollama for RTX 5070
# RTX 5070 has 12GB VRAM — optimal for 32B models with quantization
mkdir -p /etc/systemd/system/ollama.service.d/

cat > /etc/systemd/system/ollama.service.d/override.conf << 'EOF'
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_NUM_PARALLEL=2"
Environment="OLLAMA_MAX_LOADED_MODELS=2"
Environment="CUDA_VISIBLE_DEVICES=0"
Environment="OLLAMA_FLASH_ATTENTION=1"
EOF

systemctl daemon-reload
systemctl enable ollama
systemctl restart ollama

echo '{"step":"ollama_configured","ok":true,"vram_target":"12GB RTX 5070"}'

# Wait for Ollama to be ready
sleep 3
for i in {1..10}; do
    if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
        echo '{"step":"ollama_ready","ok":true}'
        break
    fi
    sleep 2
done

# Pull models — chosen for RTX 5070 12GB VRAM
# qwen2.5-coder:32b-instruct-q4_K_M fits in 12GB and is best-in-class for code
echo '{"step":"pulling_models","message":"This will take a while..."}'

ollama pull qwen2.5-coder:32b-instruct-q4_K_M
echo '{"step":"model_pulled","model":"qwen2.5-coder:32b-instruct-q4_K_M","ok":true}'

# Smaller fast model for quick tasks
ollama pull qwen2.5-coder:7b-instruct-q8_0
echo '{"step":"model_pulled","model":"qwen2.5-coder:7b-instruct-q8_0","ok":true}'

# Embedding model for semantic search
ollama pull nomic-embed-text
echo '{"step":"model_pulled","model":"nomic-embed-text","ok":true}'

echo '{"step":"complete","ok":true,"models":["qwen2.5-coder:32b-instruct-q4_K_M","qwen2.5-coder:7b-instruct-q8_0","nomic-embed-text"]}'
