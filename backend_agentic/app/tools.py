"""The agent's tools: plain Python functions.

The docstring of each function is what the model reads to decide when to use
it, so it must be clear. Every tool returns a JSON string; the agent reads it,
and agent.py also reads the "pages" / "images" fields to build the sources.
"""

import json

from PIL import Image

from . import ollama_client
from .config import settings
from .images import find_image_file, image_url, to_base64
from .retrieval import get_retriever


def _json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False)


def search_document(query: str) -> str:
    """Search the document's text for passages relevant to `query`.
    Use short, specific queries. For a question with several parts, call this
    tool once per part. Returns passages with their page numbers."""
    passages = get_retriever().search_text(query)
    return _json({
        "pages": sorted({p["page"] for p in passages}),
        "passages": [{"page": p["page"], "text": p["text"]} for p in passages],
    })


MATCH_CANDIDATES = 8   # CLIP candidates the vision model checks, in order


def picture_matches(path, description: str) -> bool:
    """Ask the vision model: does this picture really show `description`?
    The question must be strict: asked "does it show students in white
    coats?", the 4B model said YES to students in ordinary clothes."""
    answer = ollama_client.chat([{
        "role": "user", "images": [to_base64(path)],
        "content": f'Description: "{description}".\nDoes this picture show EVERY '
                   "detail of the description (people, clothing, objects, place)? "
                   "If one detail is missing, answer NO. Answer only YES or NO."}])
    return answer.strip().upper().startswith(("YES", "OUI"))


def search_images(query: str) -> str:
    """Find a picture of the document that matches a description, e.g.
    'campus building' or 'students in a lab'. Returns the matching image
    (id, page) or an empty list if no picture of the brochure matches."""
    # CLIP alone is not reliable for this: its scores are almost equal (e.g.
    # 0.23 to 0.26) and the right picture was 6th. So CLIP proposes
    # candidates and the vision model checks them one by one.
    candidates = get_retriever().search_images(query=query, k=MATCH_CANDIDATES)
    for i in candidates:
        path = find_image_file(i["image_id"])
        if path is not None and picture_matches(path, query):
            return _json({"images": [{
                "image_id": i["image_id"], "page": i["page"], "url": image_url(i["file"]),
                "similarity": round(i["similarity"], 3), "checked": True}]})
    return _json({"images": [],
                  "note": "No picture of the brochure matches this description."})


def get_page(page: int) -> str:
    """Read the full text of one page of the document (page numbers start at 1).
    Useful when a passage is cut or you need the surrounding details."""
    text = get_retriever().get_page(int(page))
    return _json({"pages": [int(page)] if text else [],
                  "text": text or f"Page {page} has no text."})


def identify_image(upload_id: str) -> str:
    """Find the photo the user sent in the brochure.
    `upload_id` is given in the user's message (it starts with 'upload_').
    If the same picture is printed in the brochure, returns
    "same_picture_found": true, its page and "page_text" = what the brochure
    writes on that page (use it to answer). Otherwise "same_picture_found": false."""
    path = find_image_file(upload_id)
    if path is None:
        return _json({"error": f"unknown upload id {upload_id}"})
    retriever = get_retriever()
    images = retriever.search_images(image=Image.open(path))
    same = [i for i in images if i["similarity"] >= settings.image_min_similarity_photo]
    if not same:
        return _json({
            "same_picture_found": False, "pages": [], "images": [],
            "note": "This photo is not in the brochure. Do not guess who or "
                    "what it is; only describe what is visible."})
    best = same[0]
    return _json({
        "same_picture_found": True,
        "page": best["page"],
        "pages": [best["page"]],
        # The facts the model may use: the brochure's own text on that page
        # (titles and captions say who or what is in the picture).
        "page_text": retriever.get_page(best["page"]),
        "images": [{"image_id": i["image_id"], "page": i["page"],
                    "url": image_url(i["file"]),
                    "similarity": round(i["similarity"], 3)} for i in same]})


# Rules for the vision model when it only looks at a picture.
LOOK_RULES = """Describe only what is really visible in the image: objects, \
places, actions, clothing, text written in the image. NEVER say who a person \
is, never give or guess a name, a job or a nationality from a face or an \
appearance. Answer in French, in 2-3 sentences."""


def look_at_image(image_id: str, question: str) -> str:
    """Look at one image and describe what is visible (never who a person is).
    `image_id` is either the user's photo ('upload_...') or a brochure image
    id returned by search_images or identify_image."""
    path = find_image_file(image_id)
    if path is None:
        return _json({"error": f"unknown image id {image_id}"})
    description = ollama_client.chat([
        {"role": "system", "content": LOOK_RULES},
        {"role": "user", "content": question, "images": [to_base64(path)]}])
    return _json({"image_id": image_id, "answer": description})


# The list used by the MCP server (and by the agent's local fallback).
ALL_TOOLS = [search_document, search_images, get_page, identify_image, look_at_image]
