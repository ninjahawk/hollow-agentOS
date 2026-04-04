"""
sentence_transformers shim — routes SentenceTransformer.encode() through
Ollama's nomic-embed-text instead of loading torch/transformers locally.

Placed at /agentOS/sentence_transformers.py so it's found first on
PYTHONPATH=/agentOS before the real (not-installed) package.
"""

import os
import numpy as np

_OLLAMA_EMBED_URL = (
    os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")
    + "/api/embeddings"
)
_EMBED_MODEL = "nomic-embed-text"


class SentenceTransformer:
    """Drop-in replacement backed by Ollama nomic-embed-text (768 dims)."""

    def __init__(self, model_name_or_path: str = "", *args, **kwargs):
        self.model_name = model_name_or_path

    def encode(self, texts, convert_to_numpy: bool = True,
               batch_size: int = 32, show_progress_bar: bool = False,
               **kwargs):
        import httpx

        single = isinstance(texts, str)
        if single:
            texts = [texts]

        embeddings = []
        for text in texts:
            try:
                r = httpx.post(
                    _OLLAMA_EMBED_URL,
                    json={"model": _EMBED_MODEL, "prompt": str(text)},
                    timeout=20,
                )
                r.raise_for_status()
                emb = r.json().get("embedding", [])
                embeddings.append(np.array(emb, dtype=np.float32))
            except Exception:
                embeddings.append(np.zeros(768, dtype=np.float32))

        result = np.array(embeddings, dtype=np.float32)
        if single:
            return result[0]
        return result
