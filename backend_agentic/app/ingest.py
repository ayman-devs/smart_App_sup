"""Ingestion: read the sources once and store them in Qdrant.

Run it with:   python -m app.ingest

What it does, for every file in data/ :
- .pdf          → text of each page cut into token chunks + images of the page
- .txt / .md    → text cut into chunks (page = 0)
- .png/.jpg/... → the image itself
Then: chunks → BGE-M3 vectors, images → CLIP vectors, all saved in Qdrant.
"""

import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import pymupdf as fitz  # PyMuPDF
import tiktoken

from . import ollama_client, vectordb
from .config import settings
from .embeddings import ImageEmbedder, TextEmbedder
from .fact_check import remove_terms, unsupported_terms
from .retrieval import readable

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
_tokenizer = tiktoken.get_encoding("cl100k_base")


@dataclass
class Chunk:
    chunk_id: str
    text: str
    page: int
    source: str


@dataclass
class ImageItem:
    image_id: str
    path: Path
    page: int
    source: str


# ---------------------------------------------------------------- chunking

def chunk_text(text: str, size: int | None = None,
               overlap: int | None = None) -> list[str]:
    """Cut a text into pieces of `size` tokens that overlap by `overlap` tokens.

    The overlap keeps a sentence that sits on a border readable in both chunks.
    """
    size = size or settings.chunk_tokens
    overlap = settings.chunk_overlap if overlap is None else overlap
    tokens = _tokenizer.encode(" ".join(text.split()))   # also squashes spaces
    if not tokens:
        return []
    chunks, step = [], max(1, size - overlap)
    for start in range(0, len(tokens), step):
        chunks.append(_tokenizer.decode(tokens[start:start + size]))
        if start + size >= len(tokens):
            break
    return chunks


# ---------------------------------------------------------------- readers

def clean_page_text(text: str) -> str:
    """Remove running headers written with spaced letters, such as
    'L A G R A N D E  E C O L E ...': they repeat on many pages and add noise."""
    return re.sub(r"(?:\b\w ){8,}\w\b", " ", text)


def read_pdf(path: Path, images_dir: Path) -> tuple[list[Chunk], list[ImageItem]]:
    chunks: list[Chunk] = []
    images: list[ImageItem] = []
    seen_images: set[str] = set()       # the same logo is often on every page
    images_dir.mkdir(parents=True, exist_ok=True)

    with fitz.open(path) as doc:
        for page_index, page in enumerate(doc):
            page_no = page_index + 1
            text = clean_page_text(page.get_text("text"))
            for i, piece in enumerate(chunk_text(text)):
                chunks.append(Chunk(f"{path.name}:p{page_no}:c{i}", piece,
                                    page_no, path.name))

            # Images embedded in the page.
            for n, info in enumerate(page.get_images(full=True)):
                xref = info[0]
                try:
                    pix = fitz.Pixmap(doc, xref)
                    if pix.width < settings.min_image_size or \
                            pix.height < settings.min_image_size:
                        continue
                    if pix.n - pix.alpha >= 4:          # CMYK → RGB
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    digest = hashlib.md5(pix.samples).hexdigest()
                    if digest in seen_images:
                        continue
                    seen_images.add(digest)
                    out = images_dir / f"{path.stem}_p{page_no}_{n}.png"
                    pix.save(out)
                    images.append(ImageItem(out.stem, out, page_no, path.name))
                except Exception as e:                   # odd image formats
                    print(f"  skip image p{page_no}#{n}: {e}")

            # A page with almost no text is probably a designed/scanned page:
            # keep a picture of the whole page so CLIP can still find it.
            if len(text.strip()) < 40:
                out = images_dir / f"{path.stem}_p{page_no}_page.png"
                page.get_pixmap(dpi=80).save(out)
                images.append(ImageItem(out.stem, out, page_no, path.name))
    return chunks, images


def read_text_file(path: Path) -> list[Chunk]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    return [Chunk(f"{path.name}:c{i}", piece, 0, path.name)
            for i, piece in enumerate(chunk_text(text))]


def collect_sources(data_dir: Path, images_dir: Path
                    ) -> tuple[list[Chunk], list[ImageItem]]:
    chunks: list[Chunk] = []
    images: list[ImageItem] = []
    for path in sorted(data_dir.iterdir()):          # only the top folder
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        if ext == ".pdf":
            print(f"PDF   {path.name}")
            c, i = read_pdf(path, images_dir)
            chunks += c
            images += i
        elif ext in {".txt", ".md"}:
            print(f"TEXT  {path.name}")
            chunks += read_text_file(path)
        elif ext in IMAGE_EXTENSIONS:
            print(f"IMAGE {path.name}")
            # Copy it next to the PDF images so the API serves them all
            # from one folder (/images/<file>).
            images_dir.mkdir(parents=True, exist_ok=True)
            out = images_dir / path.name
            shutil.copyfile(path, out)
            images.append(ImageItem(path.stem, out, 0, path.name))
    return chunks, images


# ---------------------------------------------------------------- page summaries

SUMMARY_PROMPT = """Tu résumes une page d'une brochure d'école. Écris UNE \
seule phrase en français (40 mots maximum) qui dit de quoi parle la page et \
cite ses noms propres importants (villes, écoles, personnes, filières, \
chiffres). Utilise UNIQUEMENT des informations écrites dans le texte. \
Pas d'introduction, pas de guillemets."""


def _ask_model_for_summary(page_text: str) -> str:
    return ollama_client.chat([
        {"role": "system", "content": SUMMARY_PROMPT},
        {"role": "user", "content": page_text[:4000]}])


def make_page_summaries(chunks: list[Chunk], summarize=None) -> list[Chunk]:
    """Contextual retrieval: one extra chunk per page, "Résumé de la page N :
    ...". A short page such as 'TANGER CASABLANCA RABAT ... 19 CAMPUS' never
    says the word "villes"; its summary does, so the question "dans quelles
    villes ?" can find it.
    Each summary is checked by the automatic fact check: words that are not
    on the page are removed, so a summary cannot invent anything."""
    summarize = summarize or _ask_model_for_summary
    pages: dict[tuple[str, int], list[str]] = {}
    for c in chunks:
        if c.page > 0:
            pages.setdefault((c.source, c.page), []).append(c.text)

    summaries = []
    for (source, page), texts in sorted(pages.items()):
        page_text = "\n".join(texts)
        if len(page_text.strip()) < 40:
            continue
        summary = summarize(readable(page_text)).strip()
        summary = remove_terms(summary, unsupported_terms(summary, page_text))
        if len(summary) < 20:
            continue
        print(f"  p{page}: {summary[:90]}")
        summaries.append(Chunk(f"{source}:p{page}:summary",
                               f"Résumé de la page {page} : {summary}", page, source))
    return summaries


# ---------------------------------------------------------------- main

def ingest(client=None, text_embedder=None, image_embedder=None,
           data_dir: Path | None = None, images_dir: Path | None = None,
           summaries: bool | None = None) -> dict:
    """Index everything. Arguments can be replaced by fakes in the tests."""
    client = client or vectordb.make_client()
    text_embedder = text_embedder or TextEmbedder()
    image_embedder = image_embedder or ImageEmbedder()
    data_dir = data_dir or settings.data_dir
    images_dir = images_dir or settings.images_dir
    summaries = settings.page_summaries if summaries is None else summaries

    chunks, images = collect_sources(data_dir, images_dir)
    print(f"→ {len(chunks)} text chunks, {len(images)} images")
    if summaries and chunks:
        print("Writing one summary per page with the local model...")
        chunks += make_page_summaries(chunks)

    vectordb.recreate_collection(client, settings.text_collection, text_embedder.dim)
    if chunks:
        print("Embedding text with BGE-M3...")
        # Vectors are computed on readable text ("TANGER" → "Tanger"): models
        # understand words in capitals poorly. The stored text is unchanged.
        vectors = text_embedder.embed([readable(c.text) for c in chunks])
        vectordb.upsert(client, settings.text_collection,
                        [c.chunk_id for c in chunks], vectors,
                        [{"chunk_id": c.chunk_id, "text": c.text,
                          "page": c.page, "source": c.source} for c in chunks])

    vectordb.recreate_collection(client, settings.image_collection, image_embedder.dim)
    if images:
        print("Embedding images with CLIP...")
        vectors = image_embedder.embed_images([i.path for i in images])
        vectordb.upsert(client, settings.image_collection,
                        [i.image_id for i in images], vectors,
                        [{"image_id": i.image_id, "file": i.path.name,
                          "page": i.page, "source": i.source} for i in images])

    print("Done.")
    return {"chunks": len(chunks), "images": len(images)}


if __name__ == "__main__":
    ingest()
