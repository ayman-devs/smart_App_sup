"""HTTP API tests (classic mode for real, agent mode mocked)."""

import base64
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import agent, main
from app.config import settings


@pytest.fixture
def client(env):
    # `env` (conftest.py) already plugged a Retriever with fake models.
    with TestClient(main.app) as c:
        yield c


def _photo_b64(color=(220, 20, 20)) -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def test_chat_classic(client, env):
    r = client.post("/chat", json={"question": "application deadline?"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "classic"
    assert body["answer"] == "Fake answer (p. 1)"
    assert body["sources"][0]["page"] == 1
    # The VLM received the retrieved passage in its context.
    prompt = env["chat_calls"][-1][-1]["content"]
    assert "15 July" in prompt


def test_chat_classic_with_photo_returns_the_matching_image(client, env):
    r = client.post("/chat", json={"question": "What is this?",
                                   "image_base64": _photo_b64()})
    assert r.status_code == 200
    images = r.json()["images"]
    assert images and images[0]["page"] == 3
    assert images[0]["url"].startswith("/images/")
    # The image file exists in the images folder (served at /images/...).
    file_name = images[0]["url"].removeprefix("/images/")
    assert (settings.images_dir / file_name).exists()
    # The VLM got 2 images: the photo + the best document image,
    # and it is told on which page the photo was found.
    last_message = env["chat_calls"][-1][-1]
    assert len(last_message["images"]) == 2
    assert "page 3" in last_message["content"]
    assert r.json()["sources"][0]["page"] == 3


def test_chat_classic_without_photo_shows_only_images_of_cited_pages(client):
    r = client.post("/chat", json={"question": "application deadline?"})
    cited = {s["page"] for s in r.json()["sources"]}
    assert all(img["page"] in cited for img in r.json()["images"])


def test_chat_rejects_bad_image(client):
    r = client.post("/chat", json={"question": "hi", "image_base64": "not-an-image"})
    assert r.status_code == 400


def test_chat_rejects_empty_question(client):
    assert client.post("/chat", json={"question": ""}).status_code == 422


def test_cors_allows_flutter_web(client):
    r = client.options("/chat", headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST"})
    assert r.headers.get("access-control-allow-origin") in {"*", "http://localhost:3000"}


def test_chat_agent_mode_uses_the_agent(client, monkeypatch):
    async def fake_run(question, conversation_id, photo=None, history=None):
        return {"mode": "agent", "conversation_id": conversation_id,
                "answer": "agent says hi", "sources": [], "images": [],
                "steps": [{"tool": "search_document", "args": {"query": "x"}}],
                "seconds": 0.1}

    monkeypatch.setattr(agent, "run", fake_run)
    r = client.post("/chat", json={"question": "hello", "mode": "agent",
                                   "conversation_id": "c1"})
    assert r.status_code == 200
    assert r.json()["steps"][0]["tool"] == "search_document"


def test_health(client):
    body = client.get("/health").json()
    assert "qdrant" in body and "ollama" in body


def test_classic_prompt_has_strict_rules_and_photo_page_text(client, env):
    client.post("/chat", json={"question": "Qui est-ce ?", "image_base64": _photo_b64()})
    system, user = env["chat_calls"][-1]
    assert "Never invent a name" in system["content"]
    assert "NEVER recognise or guess who a person is" in system["content"]
    # The matched picture's page (3) is passage [1] of the context.
    assert "[1] (source: doc.pdf, page 3)" in user["content"]


def test_classic_photo_not_in_brochure(client, env):
    client.post("/chat", json={"question": "Qui est-ce ?",
                               "image_base64": _photo_b64((20, 20, 220))})
    user = env["chat_calls"][-1][-1]
    assert "NOT in the brochure" in user["content"]


def test_photo_with_meaningless_question_uses_default_question(client, env):
    client.post("/chat", json={"question": "???", "image_base64": _photo_b64()})
    user = env["chat_calls"][-1][-1]
    assert "Que montre cette photo" in user["content"]


def test_classic_not_found_shows_no_pages_or_images(client, monkeypatch):
    from app import ollama_client
    from app.rag_classic import NOT_FOUND
    monkeypatch.setattr(ollama_client, "chat", lambda messages, model=None: NOT_FOUND)
    body = client.post("/chat", json={"question": "application deadline?"}).json()
    assert body["sources"] == [] and body["images"] == []
