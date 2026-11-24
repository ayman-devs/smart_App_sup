"""Chunking, RRF fusion, ingestion and search."""

import json

from PIL import Image

from app import tools
from app.ingest import chunk_text
from app.retrieval import rrf, tokenize


# ---------------------------------------------------------------- pure functions

def test_chunk_text_respects_size_and_overlap():
    text = " ".join(f"word{i}" for i in range(1000))
    chunks = chunk_text(text, size=100, overlap=20)
    assert len(chunks) > 1
    # Consecutive chunks share some words (the overlap).
    assert set(chunks[0].split()[-5:]) & set(chunks[1].split())


def test_chunk_text_empty():
    assert chunk_text("   ") == []


def test_tokenize_removes_accents_and_case():
    assert tokenize("Ingénierie, ÉCOLE!") == ["ingenierie", "ecole"]


def test_rrf_prefers_items_found_by_both_searches():
    scores = rrf([["a", "b", "c"], ["c", "d"]], k=60)
    # "c" is in both lists, so it beats "b" which is only in one.
    assert scores["c"] > scores["b"]
    assert max(scores, key=scores.get) in {"a", "c"}


# ---------------------------------------------------------------- ingestion

def test_ingest_counts(env):
    # 2 text pages + 1 txt file → 3 chunks ; 1 picture + 1 "page image"
    # (page 3 has no text, so the whole page is saved too).
    assert env["stats"]["chunks"] == 3
    assert env["stats"]["images"] == 2
    assert any((env["data"] / "images").glob("*.png"))


# ---------------------------------------------------------------- search

def test_search_text_finds_the_right_page(env):
    results = env["retriever"].search_text("When is the application deadline?")
    assert results[0]["page"] == 1
    assert "15 July" in results[0]["text"]


def test_search_text_keywords(env):
    results = env["retriever"].search_text("robotics laboratory")
    assert results[0]["page"] == 2


def test_get_page(env):
    assert "robotics" in env["retriever"].get_page(2)
    assert env["retriever"].get_page(99) == ""


def test_search_images_with_a_photo(env):
    red_photo = Image.new("RGB", (64, 64), (220, 20, 20))
    best = env["retriever"].search_images(image=red_photo)[0]
    assert best["page"] == 3
    assert best["similarity"] > 0.9


# ---------------------------------------------------------------- tools

def test_tool_search_document_returns_pages(env):
    data = json.loads(tools.search_document("deadline"))
    assert 1 in data["pages"]


def test_tool_get_page(env):
    data = json.loads(tools.get_page(2))
    assert data["pages"] == [2]


def test_tool_unknown_image(env):
    assert "error" in json.loads(tools.look_at_image("upload_nope", "what?"))


def test_clean_page_text_removes_spaced_headers():
    from app.ingest import clean_page_text
    cleaned = clean_page_text("L A G R A N D E E C O L E\nCampus de Rabat, bloc A B")
    assert "L A G R" not in cleaned
    assert "Campus de Rabat, bloc A B" in cleaned


# ---------------------------------------------------------------- anti-hallucination

def test_identify_image_returns_the_page_text_when_the_picture_is_in_the_brochure(env):
    from app.images import save_upload
    upload = save_upload(Image.new("RGB", (64, 64), (220, 20, 20)))  # the red picture
    data = json.loads(tools.identify_image(upload))
    assert data["same_picture_found"] is True
    assert data["page"] == 3
    assert "page_text" in data


def test_identify_image_says_when_the_photo_is_not_in_the_brochure(env):
    from app.images import save_upload
    upload = save_upload(Image.new("RGB", (64, 64), (20, 20, 220)))  # blue: not in it
    data = json.loads(tools.identify_image(upload))
    assert data["same_picture_found"] is False
    assert data["pages"] == []


def test_look_at_image_forbids_identifying_people(env):
    from app.images import save_upload
    upload = save_upload(Image.new("RGB", (64, 64), "white"))
    tools.look_at_image(upload, "Qui est cette personne ?")
    system = env["chat_calls"][-1][0]
    assert system["role"] == "system"
    assert "NEVER say who a person is" in system["content"]


# ---------------------------------------------------------------- retrieval quality

def test_readable_fixes_words_in_capitals():
    from app.retrieval import readable
    assert readable("TANGER CASABLANCA, 19 CAMPUS de l'EMSI") == \
        "Tanger Casablanca, 19 Campus de l'EMSI"


def test_page_summaries_cannot_invent(env):
    from app.ingest import Chunk, make_page_summaries
    chunks = [Chunk("b.pdf:p2:c0", "TANGER CASABLANCA RABAT MARRAKECH FES UN RÉSEAU DE 19 CAMPUS",
                    2, "b.pdf")]

    def fake_model(page_text):
        # A summary with one invented city (Agadir).
        return "Le réseau de 19 campus de l'EMSI dans les villes de Tanger, Rabat, Agadir"

    summary = make_page_summaries(chunks, summarize=fake_model)[0]
    assert summary.chunk_id == "b.pdf:p2:summary"
    assert summary.text.startswith("Résumé de la page 2 :")
    assert "Tanger" in summary.text and "Agadir" not in summary.text


def test_get_page_ignores_summaries(env):
    retriever = env["retriever"]
    retriever.chunks.append({"chunk_id": "doc.pdf:p2:summary", "page": 2,
                             "source": "doc.pdf", "text": "Résumé de la page 2"})
    assert "Résumé" not in retriever.get_page(2)


def test_reranker_is_combined_with_hybrid_ranking(env):
    class ReversingReranker:
        """Puts the best hybrid result last."""
        def score(self, query, passages):
            return list(range(len(passages)))

    retriever = env["retriever"]
    plain = retriever.search_text("robotics laboratory")
    retriever.reranker = ReversingReranker()
    combined = retriever.search_text("robotics laboratory")
    retriever.reranker = None
    # Alone, the reranker would put the right page last; combined, it stays first.
    assert plain[0]["page"] == 2 and combined[0]["page"] == 2


def test_search_images_returns_only_a_picture_the_model_confirmed(env, monkeypatch):
    # CLIP proposes candidates; the vision model says YES only for one of them.
    import json
    from app import ollama_client, tools
    first = json.loads(tools.search_images("students in a lab"))
    assert first["note"]                     # the fake chat never says YES
    candidates = env["retriever"].search_images(query="students in a lab", k=8)
    assert candidates, "the test PDF has images"
    wanted = candidates[-1]["image_id"]
    seen = []

    def fake_chat(messages, model=None):
        image = messages[0]["images"][0]
        seen.append(image)
        return "YES" if len(seen) == len(candidates) else "NO"

    monkeypatch.setattr(ollama_client, "chat", fake_chat)
    found = json.loads(tools.search_images("students in a lab"))
    assert [i["image_id"] for i in found["images"]] == [wanted]
