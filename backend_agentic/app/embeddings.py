"""Turn text and images into vectors.

- Text  → BGE-M3 (through Ollama), 1024 numbers per text.
- Image → CLIP ViT-B/32, 512 numbers per image.
- To search images *with words*, we use a multilingual CLIP text encoder that
  writes into the same 512-number space as the image model, so a French
  question can match a picture.

The heavy models are loaded lazily (on first use) so that importing this file
is fast, and tests can replace these classes with fakes.
"""

from functools import cached_property
from pathlib import Path

from PIL import Image

from . import ollama_client
from .config import settings

TEXT_DIM = 1024   # BGE-M3
IMAGE_DIM = 512   # CLIP ViT-B/32


class TextEmbedder:
    dim = TEXT_DIM

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        # Small batches: Ollama handles them fine and we show progress.
        for i in range(0, len(texts), 16):
            vectors.extend(ollama_client.embed(texts[i:i + 16]))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]


class ImageEmbedder:
    dim = IMAGE_DIM

    @cached_property
    def _image_model(self):
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer(settings.clip_image_model, device="cpu")

    @cached_property
    def _text_model(self):
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer(settings.clip_text_model, device="cpu")

    def embed_images(self, images: list[Image.Image | Path]) -> list[list[float]]:
        pil = [Image.open(i).convert("RGB") if isinstance(i, Path) else i.convert("RGB")
               for i in images]
        return self._image_model.encode(pil, normalize_embeddings=True).tolist()

    def embed_text(self, text: str) -> list[float]:
        """Text → CLIP space, to find images that match a sentence."""
        return self._text_model.encode([text], normalize_embeddings=True)[0].tolist()


class Reranker:
    """Cross-encoder: reads (question, passage) together and scores relevance.
    Slower than vectors but much more precise, so we use it on ~20 candidates."""

    @cached_property
    def _model(self):
        from sentence_transformers import CrossEncoder
        return CrossEncoder(settings.reranker_model, device="cpu")

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        return [float(s) for s in self._model.predict([(query, p) for p in passages])]
