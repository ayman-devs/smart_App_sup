"""Very small HTTP client for Ollama (http://localhost:11434).

We only need two things:
- embed(): turn texts into vectors (BGE-M3);
- chat():  ask the vision-language model a question, optionally with images.
"""

import time

import httpx

from .config import settings


class OllamaError(RuntimeError):
    """Raised when Ollama is not running or a model is missing."""


def _post(path: str, payload: dict, retries: int = 1) -> dict:
    try:
        response = httpx.post(
            f"{settings.ollama_url}{path}",
            json=payload,
            timeout=settings.ollama_timeout,
        )
    except httpx.HTTPError as e:
        raise OllamaError(
            f"Cannot reach Ollama at {settings.ollama_url}. Is it running? ({e})"
        ) from e
    if response.status_code >= 500 and retries > 0:
        # Ollama sometimes fails once under load (seen in testing): retry.
        time.sleep(1)
        return _post(path, payload, retries - 1)
    if response.status_code != 200:
        raise OllamaError(f"Ollama error {response.status_code}: {response.text}")
    return response.json()


def embed(texts: list[str], model: str | None = None) -> list[list[float]]:
    """Return one vector per text."""
    data = _post("/api/embed", {"model": model or settings.ollama_embed_model,
                                "input": texts})
    return data["embeddings"]


def chat(messages: list[dict], model: str | None = None) -> str:
    """Send a chat to the model and return its answer.

    A message looks like {"role": "user", "content": "...", "images": [b64, ...]}
    where images are base64 strings (no "data:image/..." prefix).
    """
    data = _post("/api/chat", {
        "model": model or settings.ollama_vlm_model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.2, "num_ctx": settings.ollama_num_ctx,
                    "num_predict": settings.ollama_num_predict},
    })
    return data["message"]["content"].strip()


def list_models() -> list[str]:
    """Names of the models installed in Ollama (used by /health)."""
    try:
        r = httpx.get(f"{settings.ollama_url}/api/tags", timeout=5)
        return [m["name"] for m in r.json().get("models", [])]
    except httpx.HTTPError:
        return []
