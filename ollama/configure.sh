#!/bin/bash
set -e
mkdir -p /etc/systemd/system/ollama.service.d/
cat > /etc/systemd/system/ollama.service.d/override.conf << 'EOF'
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_NUM_PARALLEL=2"
Environment="OLLAMA_MAX_LOADED_MODELS=2"
Environment="CUDA_VISIBLE_DEVICES=0"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_MODELS=/mnt/c/Users/jedin/.ollama/models"
EOF
systemctl daemon-reload
systemctl restart ollama
sleep 2
ollama list
