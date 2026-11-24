"""FastAPI app: the HTTP API used by the Flutter app.

Start it with:   uvicorn app.main:app --port 8000
Interactive docs: http://localhost:8000/docs
"""

import asyncio
import re
import contextlib
import uuid
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agent, multi_agent, ollama_client, rag_classic, vectordb
from .config import settings
from .images import decode_base64_image
from .mcp_server import build_mcp
from .ollama_client import OllamaError
from .retrieval import get_retriever


# ---------------------------------------------------------------- startup

class _McpMount:
    """Forwards /mcp/ requests to the MCP server created at startup."""
    app = None

    async def __call__(self, scope, receive, send):
        await self.app(scope, receive, send)


_mcp_mount = _McpMount()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # The MCP server needs its session manager running while the API runs.
    mcp = build_mcp()
    _mcp_mount.app = mcp.streamable_http_app()
    async with mcp.session_manager.run():
        # Load the search models now (CLIP, reranker, BM25) instead of on the
        # first question. If Qdrant is down, we still start: /health says why.
        try:
            retriever = await asyncio.to_thread(get_retriever)
            await asyncio.to_thread(retriever.warm_up)
        except Exception as e:  # noqa: BLE001
            print(f"[startup] retriever not ready: {e}")
        yield


app = FastAPI(title="Agentic Multimodal RAG", lifespan=lifespan)

# CORS: lets Flutter web (another port, e.g. localhost:3000) call this API.
# "*" is fine on your own computer; list real origins before deploying.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Images extracted from the PDF: GET /images/<file>.png
settings.images_dir.mkdir(parents=True, exist_ok=True)
app.mount("/images", StaticFiles(directory=settings.images_dir), name="images")

# MCP server (tools for the agent): /mcp/
app.mount("/mcp", _mcp_mount)


# ---------------------------------------------------------------- models

class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    image_base64: str | None = None      # optional photo (plain or data: URL)
    mode: Literal["classic", "agent", "multi"] = "classic"
    conversation_id: str | None = None   # agent memory; one per conversation
    # Past messages of this conversation (saved by the app in Supabase). Used
    # only if the agent's memory is empty: backend restarted, chat reopened.
    history: list[HistoryMessage] = Field(default_factory=list, max_length=200)


class ResetRequest(BaseModel):
    conversation_id: str


PHOTO_DEFAULT_QUESTION = "Que montre cette photo et que dit la brochure à son sujet ?"


def _photo(request: ChatRequest):
    if not request.image_base64:
        return None
    try:
        return decode_base64_image(request.image_base64)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


async def _run(request: ChatRequest, mode: str) -> dict:
    """Run one mode; turn model / database errors into clear HTTP errors."""
    photo = _photo(request)
    if photo is not None and not re.search(r"[^\W\d_]", request.question):
        # "???" or "..." with a photo: ask the useful question instead.
        request = request.model_copy(update={"question": PHOTO_DEFAULT_QUESTION})
    history = [h.model_dump() for h in request.history]
    try:
        if mode == "classic":
            return await run_in_threadpool(rag_classic.answer,
                                           request.question, photo)
        if mode == "multi":
            return await multi_agent.run(request.question,
                                         request.conversation_id or "default", photo,
                                         history=history)
        return await agent.run(request.question,
                               request.conversation_id or "default", photo,
                               history=history)
    except OllamaError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500,
                            detail=f"{type(e).__name__}: {e}") from e


# ---------------------------------------------------------------- routes

@app.get("/health")
def health():
    """Is everything ready? Qdrant, documents indexed, Ollama models."""
    try:
        client = vectordb.make_client()
        qdrant = {"ok": True,
                  "text_chunks": vectordb.count(client, settings.text_collection),
                  "images": vectordb.count(client, settings.image_collection)}
    except Exception as e:  # noqa: BLE001
        qdrant = {"ok": False, "error": str(e)}

    installed = ollama_client.list_models()

    def has(model: str) -> bool:
        return any(m == model or m.split(":")[0] == model for m in installed)

    ollama = {"ok": bool(installed),
              "vlm_model": settings.ollama_vlm_model,
              "vlm_installed": has(settings.ollama_vlm_model),
              "embed_model": settings.ollama_embed_model,
              "embed_installed": has(settings.ollama_embed_model)}
    ready = qdrant["ok"] and qdrant.get("text_chunks", 0) > 0 and \
        ollama["vlm_installed"] and ollama["embed_installed"]
    return {"status": "ok" if ready else "not_ready", "qdrant": qdrant,
            "ollama": ollama, "agent_tools": agent.tool_source()}


@app.post("/chat")
async def chat(request: ChatRequest):
    """Question (text + optional photo) in classic, agent or multi mode."""
    return await _run(request, request.mode)


@app.post("/agent/chat")
async def agent_chat(request: ChatRequest):
    """Agent mode, with memory per conversation_id."""
    return await _run(request, "agent")


@app.post("/agent/reset")
def agent_reset(request: ResetRequest):
    """Clear the memory of a conversation (agent and multi-agent modes)."""
    agent.reset(request.conversation_id)
    multi_agent.reset(request.conversation_id)
    return {"ok": True}


@app.post("/compare")
async def compare(request: ChatRequest):
    """Same question to the 3 modes, to compare time, pages and tool calls."""
    results = {"classic": await _run(request, "classic")}
    for mode, module in [("agent", agent), ("multi", multi_agent)]:
        # A fresh conversation, so no mode has a memory advantage.
        fresh = request.model_copy(
            update={"conversation_id": f"cmp_{uuid.uuid4().hex[:8]}"})
        results[mode] = await _run(fresh, mode)
        module.reset(fresh.conversation_id)
    return {
        "question": request.question,
        **results,
        "summary": {
            mode: {"seconds": r["seconds"],
                   "pages": [s["page"] for s in r["sources"]],
                   "tool_calls": len(r["steps"]),
                   "agents": r.get("agents", [])}
            for mode, r in results.items()},
    }
