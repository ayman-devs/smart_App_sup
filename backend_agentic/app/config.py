"""All settings in one place.

Every value can be overridden with an environment variable of the same name
(upper case), or in a `.env` file next to this folder. Example:
    OLLAMA_VLM_MODEL=qwen3-vl:8b-instruct
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend_agentic/ (the folder that contains app/)
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    # --- Server ---
    api_port: int = 8000
    # URL the agent uses to reach the MCP server (mounted inside this same API).
    mcp_url: str = "http://127.0.0.1:8000/mcp/"

    # --- Ollama (local models) ---
    ollama_url: str = "http://localhost:11434"
    ollama_vlm_model: str = "qwen3-vl:4b-instruct"   # reads text, sees images, calls tools
    ollama_embed_model: str = "bge-m3"               # multilingual text embeddings
    ollama_timeout: float = 300.0                    # seconds (first call loads the model)
    # Context window of the model, in tokens. Ollama's default (~4k) is too
    # small: the agent's tool results alone can fill it, and the answer is
    # cut off. 16k fits in 8 GB of GPU memory with a 4B model.
    ollama_num_ctx: int = 16384
    # Maximum length of one model answer, in tokens. Stops a model stuck in a
    # loop (seen in testing: a list repeated for 2 minutes).
    ollama_num_predict: int = 1024

    # --- Qdrant ---
    # "http://localhost:6333" for Docker, or ":memory:" for tests.
    qdrant_url: str = "http://localhost:6333"
    text_collection: str = "doc_text"
    image_collection: str = "doc_images"

    # --- CLIP (image embeddings) ---
    clip_image_model: str = "clip-ViT-B-32"
    clip_text_model: str = "sentence-transformers/clip-ViT-B-32-multilingual-v1"

    # --- Reranker (multilingual cross-encoder) ---
    reranker_model: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"

    # --- Data ---
    data_dir: Path = BASE_DIR / "data"          # put your PDF / txt / images here
    images_dir: Path = BASE_DIR / "data" / "images"    # images extracted from the PDF
    uploads_dir: Path = BASE_DIR / "data" / "uploads"  # photos sent by the app

    # --- Chunking ---
    chunk_tokens: int = 350
    chunk_overlap: int = 60
    min_image_size: int = 120   # ignore icons / tiny images (pixels)
    # One AI-written summary per page, indexed as an extra passage
    # ("contextual retrieval"). Slower ingestion (~5 s per page), better search.
    page_summaries: bool = True

    # --- Retrieval ---
    top_k_vector: int = 20      # candidates from vector search
    top_k_bm25: int = 20        # candidates from keyword search
    top_k_final: int = 5        # passages kept after reranking
    top_k_images: int = 3
    rrf_k: int = 60             # standard RRF constant
    # An image is shown to the user only if CLIP similarity is above this.
    # Text → image scores are low by nature (~0.22-0.30 on the brochure);
    # photo → image scores are high: ~0.96 for the same picture (even
    # cropped / recompressed), ~0.83 for a different but similar photo.
    # Classic mode without photo uses the cited pages instead (rag_classic.py).
    image_min_similarity_text: float = 0.24
    image_min_similarity_photo: float = 0.9

    # --- Agent memory ---
    # Older messages are trimmed above this (must stay below ollama_num_ctx,
    # with room for the system prompt, tool descriptions and the answer).
    memory_max_tokens: int = 10000


settings = Settings()
