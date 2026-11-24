"""Hybrid + multimodal search.

Text search (find the right passages):
  1. vector search  : BGE-M3 vector of the question vs. chunk vectors (meaning)
  2. BM25 search    : classic keyword search (exact words, names, numbers)
  3. RRF fusion     : merge the two ranked lists
  4. reranking      : a cross-encoder re-reads the best candidates and sorts them

Image search (find the right pictures):
  - question text → multilingual CLIP text vector → nearest images
  - user photo    → CLIP image vector              → nearest images
  - search_images() can merge both lists with RRF; for a question with a
    photo, multimodal_search() uses the photo alone (more reliable).
"""

import re
import unicodedata

from PIL import Image
from rank_bm25 import BM25Okapi

from .config import settings
from . import vectordb


# ---------------------------------------------------------------- helpers

def tokenize(text: str) -> list[str]:
    """Lower case, no accents, words only: 'Ingénierie!' → ['ingenierie']."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.findall(r"\w+", text)


def readable(text: str) -> str:
    """'TANGER CASABLANCA RABAT' → 'Tanger Casablanca Rabat'.
    Embedding models and the reranker understand words written in capitals
    poorly (measured on the brochure: reranker score -3.7 → -1.4)."""
    return " ".join(w.capitalize() if w.isupper() and len(w) > 1 else w
                    for w in text.split())


def rrf(rankings: list[list[str]], k: int | None = None) -> dict[str, float]:
    """Reciprocal Rank Fusion.

    Each list is a ranking of ids (best first). An id gets 1 / (k + rank) from
    every list it appears in; being near the top of several lists wins.
    Returns {id: score}, not sorted.
    """
    k = k or settings.rrf_k
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item_id in enumerate(ranking, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    return scores


def _chunk_order(chunk_id: str) -> int:
    """'file.pdf:p3:c2' → 2 (position of the chunk inside its page)."""
    match = re.search(r":c(\d+)$", chunk_id)
    return int(match.group(1)) if match else 0


# ---------------------------------------------------------------- retriever

class Retriever:
    def __init__(self, client, text_embedder, image_embedder, reranker=None):
        self.client = client
        self.text_embedder = text_embedder
        self.image_embedder = image_embedder
        self.reranker = reranker          # None = skip reranking
        self.refresh()

    def warm_up(self) -> None:
        """Load the heavy models now (at API start) rather than on the first
        question. The first run also downloads them from Hugging Face."""
        for obj, attr in [(self.image_embedder, "_image_model"),
                          (self.image_embedder, "_text_model"),
                          (self.reranker, "_model")]:
            if obj is not None and hasattr(type(obj), attr):
                getattr(obj, attr)

    def refresh(self) -> None:
        """(Re)load all chunks from Qdrant and build the BM25 index in memory."""
        self.chunks = vectordb.all_payloads(self.client, settings.text_collection)
        self.by_id = {c["chunk_id"]: c for c in self.chunks}
        self.bm25 = BM25Okapi([tokenize(c["text"]) for c in self.chunks]) \
            if self.chunks else None

    # ------------------------------------------------------------ text

    def search_text(self, query: str, k: int | None = None) -> list[dict]:
        k = k or settings.top_k_final
        if not self.chunks:
            return []

        # 1. Vector search (meaning).
        vector_hits = vectordb.search(
            self.client, settings.text_collection,
            self.text_embedder.embed_query(query), settings.top_k_vector)
        vector_ranking = [p["chunk_id"] for p, _ in vector_hits]

        # 2. BM25 (keywords).
        bm25_ranking: list[str] = []
        query_tokens = tokenize(query)
        if self.bm25 and query_tokens:
            scores = self.bm25.get_scores(query_tokens)
            best = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
            bm25_ranking = [self.chunks[i]["chunk_id"]
                            for i in best[:settings.top_k_bm25] if scores[i] > 0]

        # 3. RRF fusion.
        fused = rrf([vector_ranking, bm25_ranking])
        candidates = sorted(fused, key=fused.get, reverse=True)[:settings.top_k_vector]
        results = [dict(self.by_id[cid], score=fused[cid])
                   for cid in candidates if cid in self.by_id]

        # 4. Reranking with the cross-encoder, combined with the hybrid
        #    ranking (RRF again) instead of replacing it: alone, the reranker
        #    pushed a short but exact passage from rank 1 down to rank 9.
        if self.reranker and results:
            scores = self.reranker.score(query, [readable(r["text"]) for r in results])
            reranked = [r["chunk_id"] for _, r in
                        sorted(zip(scores, results), key=lambda x: x[0], reverse=True)]
            final = rrf([[r["chunk_id"] for r in results], reranked])
            for r in results:
                r["score"] = final[r["chunk_id"]]
            results.sort(key=lambda r: r["score"], reverse=True)
        return results[:k]

    def get_page(self, page: int, source: str | None = None) -> str:
        """Full text of one page, rebuilt from its chunks."""
        parts = [c for c in self.chunks if c["page"] == page
                 and (source is None or c["source"] == source)
                 and not c["chunk_id"].endswith(":summary")]   # real text only
        parts.sort(key=lambda c: _chunk_order(c["chunk_id"]))
        # Chunks overlap a little; good enough to read the page.
        return "\n".join(c["text"] for c in parts)

    # ------------------------------------------------------------ images

    def search_images(self, query: str | None = None,
                      image: Image.Image | None = None,
                      k: int | None = None) -> list[dict]:
        k = k or settings.top_k_images
        rankings: list[list[str]] = []
        payloads: dict[str, dict] = {}
        similarity: dict[str, float] = {}

        def add(hits):
            rankings.append([p["image_id"] for p, _ in hits])
            for p, score in hits:
                payloads[p["image_id"]] = p
                similarity[p["image_id"]] = max(score, similarity.get(p["image_id"], -1))

        if query:
            add(vectordb.search(self.client, settings.image_collection,
                                self.image_embedder.embed_text(query), k * 3))
        if image is not None:
            add(vectordb.search(self.client, settings.image_collection,
                                self.image_embedder.embed_images([image])[0], k * 3))
        if not rankings:
            return []
        fused = rrf(rankings)
        best = sorted(fused, key=fused.get, reverse=True)[:k]
        # "similarity" = CLIP cosine, useful to know if a match is convincing.
        return [dict(payloads[i], score=fused[i], similarity=similarity[i])
                for i in best]

    # ------------------------------------------------------------ both

    def multimodal_search(self, question: str,
                          image: Image.Image | None = None) -> dict:
        # With a photo, the photo alone drives the image search: mixing in the
        # question text ("???", "qui est-ce ?") pushes random pictures that
        # appear in both lists above the real match.
        images = (self.search_images(image=image) if image is not None
                  else self.search_images(query=question))
        return {"passages": self.search_text(question), "images": images}


# ---------------------------------------------------------------- shared instance

_retriever: Retriever | None = None


def get_retriever() -> Retriever:
    """One Retriever for the whole app (models are loaded only once)."""
    global _retriever
    if _retriever is None:
        from .embeddings import ImageEmbedder, Reranker, TextEmbedder
        _retriever = Retriever(vectordb.make_client(), TextEmbedder(),
                               ImageEmbedder(), Reranker())
    return _retriever


def set_retriever(retriever: Retriever | None) -> None:
    """Used by the tests to plug in a Retriever built with fake models."""
    global _retriever
    _retriever = retriever
