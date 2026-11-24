"""Shared test setup.

No real model and no Docker here:
- Qdrant runs in memory (":memory:");
- the embedders are small fakes that turn words / pixels into vectors;
- Ollama's chat is replaced by a function that returns a fixed answer.
"""

import hashlib
import math

import pymupdf as fitz
import pytest
from PIL import Image

from app import ollama_client, retrieval, vectordb
from app.config import settings
from app.ingest import ingest


def _normalize(v: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def _hash_words(text: str, dim: int) -> list[float]:
    """Bag of words hashed into `dim` buckets: same words → close vectors."""
    v = [0.0] * dim
    for word in retrieval.tokenize(text):
        v[int(hashlib.md5(word.encode()).hexdigest(), 16) % dim] += 1.0
    return _normalize(v)


class FakeTextEmbedder:
    dim = 64

    def embed(self, texts):
        return [_hash_words(t, self.dim) for t in texts]

    def embed_query(self, text):
        return _hash_words(text, self.dim)


class FakeImageEmbedder:
    """An image becomes its 4x4 thumbnail colours (48 numbers)."""
    dim = 48

    def embed_images(self, images):
        out = []
        for img in images:
            img = (Image.open(img) if not isinstance(img, Image.Image) else img)
            pixels = img.convert("RGB").resize((4, 4)).tobytes()  # 48 bytes
            out.append(_normalize([c / 255 + 0.01 for c in pixels]))
        return out

    def embed_text(self, text):
        return _hash_words(text, self.dim)


def make_pdf(path):
    """A 3-page PDF: two text pages and one page with a red picture."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Admissions: the application deadline is 15 July.")
    page = doc.new_page()
    page.insert_text((72, 72), "Campus Casablanca offers a robotics laboratory.")
    page = doc.new_page()
    red = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 200), False)
    red.set_rect(red.irect, (220, 20, 20))
    page.insert_image(fitz.Rect(72, 72, 272, 272), pixmap=red)
    doc.save(path)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Ingest a small PDF into in-memory Qdrant and plug a fake Retriever."""
    data = tmp_path / "data"
    data.mkdir()
    make_pdf(data / "doc.pdf")
    (data / "notes.txt").write_text("Tuition fees are paid in three instalments.",
                                    encoding="utf-8")

    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(settings, "images_dir", data / "images")
    monkeypatch.setattr(settings, "uploads_dir", data / "uploads")

    client = vectordb.make_client(":memory:")
    stats = ingest(client, FakeTextEmbedder(), FakeImageEmbedder(), summaries=False)
    retriever = retrieval.Retriever(client, FakeTextEmbedder(), FakeImageEmbedder())
    retrieval.set_retriever(retriever)

    # Fake VLM: records what it received and answers a fixed text.
    calls = []

    def fake_chat(messages, model=None):
        calls.append(messages)
        return "Fake answer (p. 1)"

    monkeypatch.setattr(ollama_client, "chat", fake_chat)
    monkeypatch.setattr(ollama_client, "list_models",
                        lambda: [settings.ollama_vlm_model, settings.ollama_embed_model])

    yield {"retriever": retriever, "stats": stats, "chat_calls": calls,
           "data": data}
    retrieval.set_retriever(None)
