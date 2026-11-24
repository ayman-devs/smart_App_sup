"""Classic RAG: always the same path.

    question (+ photo) → one multimodal search → context → one VLM call → answer
"""

import time

from PIL import Image

from . import ollama_client
from .config import settings
from .images import image_url, to_base64
from .retrieval import Retriever, get_retriever

# Sentence used when the brochure does not contain the answer.
NOT_FOUND = "Je n'ai pas trouvé cette information dans la brochure EMSI."

# Rules shared by the classic mode and the agent (agent.py imports them).
# Small local models invent facts easily, so the rules are short and strict.
GROUNDING_RULES = f"""STRICT RULES (always follow them):
1. Your ONLY knowledge is the EMSI brochure (the context or tool results \
given to you). Never use general knowledge. Never invent a name, a number, \
a date, a person, a job or any fact.
2. Every fact in your answer must come from the brochure, with its page: (p. 3).
3. If the brochure does not contain the answer, reply exactly: "{NOT_FOUND}" \
(if you have tools, only after searching with them).
4. If the question is not about the EMSI school or its brochure, reply that \
you only answer questions about the EMSI brochure.
5. ONLY FOR PHOTOS of people: NEVER recognise or guess who a person is from \
their face or appearance. You may only repeat what the brochure writes on the \
page where this exact picture is printed (for example a title or a caption), \
and say that it comes from that page. Otherwise say you cannot identify people. \
(Text questions about people NAMED in the brochure, such as the director, \
professors or graduates, are normal questions: answer them from the brochure.)
6. Describe only what is really visible in an image. Do not guess.
7. Answer in the language of the question (French if it is in French), concisely."""

SYSTEM_PROMPT = f"""You are the EMSI brochure assistant of a mobile app. You \
answer using ONLY the context passages given to you.
If images are attached: the first is the user's photo, the next one is the \
matching picture from the brochure.

{GROUNDING_RULES}"""


def build_context(passages: list[dict]) -> str:
    """Numbered passages with their page, given to the model."""
    if not passages:
        return "(no passage found)"
    return "\n\n".join(
        f"[{i}] (source: {p['source']}, page {p['page']})\n{p['text']}"
        for i, p in enumerate(passages, start=1))


def keep_good_images(images: list[dict], with_photo: bool) -> list[dict]:
    """Keep only convincing CLIP matches, so we don't show random pictures.
    (Used for photo searches here, and for the agent's image tools.)"""
    threshold = (settings.image_min_similarity_photo if with_photo
                 else settings.image_min_similarity_text)
    return [img for img in images if img["similarity"] >= threshold]


def format_sources(passages: list[dict]) -> list[dict]:
    """Unique (source, page) pairs, in order of relevance."""
    seen, sources = set(), []
    for p in passages:
        key = (p["source"], p["page"])
        if key not in seen:
            seen.add(key)
            sources.append({"source": p["source"], "page": p["page"]})
    return sources


def format_images(images: list[dict]) -> list[dict]:
    return [{"image_id": img["image_id"], "url": image_url(img["file"]),
             "page": img["page"], "similarity": round(img["similarity"], 3)}
            for img in images]


def answer(question: str, photo: Image.Image | None = None,
           retriever: Retriever | None = None) -> dict:
    retriever = retriever or get_retriever()
    start = time.perf_counter()

    # 1. One multimodal search.
    found = retriever.multimodal_search(question, photo)
    passages = found["passages"]
    if photo is not None:
        # Photo → image scores are reliable: keep only close matches.
        images = keep_good_images(found["images"], with_photo=True)
        if images:
            # The photo is printed in the brochure: the text of that page
            # (title, caption) is what the brochure says about it. Put it first.
            page = images[0]["page"]
            page_passage = {"source": images[0]["source"], "page": page,
                            "text": retriever.get_page(page)}
            passages = [page_passage] + [p for p in passages if p["page"] != page]
    else:
        # Text → image scores are not reliable alone (a logo on many pictures
        # can win). Keep images from the pages used for the answer, so the
        # picture always matches the text.
        pages = {p["page"] for p in passages}
        images = [img for img in retriever.search_images(question, k=20)
                  if img["page"] in pages][:settings.top_k_images]

    # 2. Images for the VLM: the user's photo + the best document image.
    vlm_images = []
    if photo is not None:
        vlm_images.append(to_base64(photo))
    if images:
        path = settings.images_dir / images[0]["file"]
        if path.exists():
            vlm_images.append(to_base64(path))

    # 3. One model call.
    # If the photo was found in the document, say where: the model cannot
    # guess a page number from pixels.
    photo_note = ""
    if photo is not None and images:
        photo_note = (f"\nNote: the user's photo is the picture printed on page "
                      f"{images[0]['page']} of the brochure (similarity "
                      f"{images[0]['similarity']:.2f}). Passage [1] is the text "
                      f"of that page: use only it to say what the brochure "
                      f"writes about this picture.\n")
    elif photo is not None:
        photo_note = ("\nNote: the user's photo is NOT in the brochure. Do not "
                      "guess who or what it is; only describe what is visible.\n")
    user_message = {
        "role": "user",
        "content": f"Context:\n{build_context(passages)}\n{photo_note}"
                   f"\nQuestion: {question}",
    }
    if vlm_images:
        user_message["images"] = vlm_images
    text = ollama_client.chat([{"role": "system", "content": SYSTEM_PROMPT},
                               user_message])
    refused = NOT_FOUND in text or "uniquement aux questions" in text

    return {
        "mode": "classic",
        "answer": text,
        # The page of the matched picture comes first, then the passages.
        # Nothing to show when the answer is "not found" or a refusal.
        "sources": [] if refused else format_sources(
            [{"source": img["source"], "page": img["page"]} for img in images[:1]]
            + passages),
        "images": [] if refused else format_images(images),
        "steps": [],
        "seconds": round(time.perf_counter() - start, 2),
    }
