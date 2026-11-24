"""Agentic RAG: Agent = LLM + Tools + Memory.

- LLM    : the local VLM through ChatOllama.
- Tools  : read from our MCP server (langchain-mcp-adapters). If the MCP
           server cannot be reached, the same Python functions are used directly.
- Memory : a LangGraph checkpointer keeps each conversation (thread_id =
           conversation_id); old messages are trimmed to a token budget.

The ReAct loop (Reasoning → Action → Observation) is built by LangGraph:
the model thinks, calls a tool, reads the result, and repeats until it answers.
"""

import asyncio
import json
import re
import time

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately, trim_messages
from langchain_core.tools import StructuredTool
from langchain_ollama import ChatOllama
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt import create_react_agent
from PIL import Image

from .config import settings
from .images import find_image_file, save_upload, to_base64
from .rag_classic import GROUNDING_RULES, format_sources
from . import tools as local_tools

SYSTEM_PROMPT = f"""You are the EMSI brochure assistant of a mobile app. You \
answer questions about the EMSI brochure by using your tools.
How to work:
- Always use a tool before answering: call search_document (several times \
with short queries if the question has several parts), also for a question \
about a person named in the brochure (e.g. "qui est le directeur ?"). Never \
say you did not find something before searching.
- Use get_page when a passage is cut or you need more details.
- If the user attached a photo, you can see it in their message, with its id \
(starting with 'upload_'). Call identify_image with that id. If it returns \
"same_picture_found": true, answer from its "page_text" (what the brochure \
writes on that page) and cite that page. If it is false, say the photo is not \
in the brochure and only describe what is visible.
- To find pictures of the brochure, use search_images then look_at_image.

{GROUNDING_RULES}"""

_checkpointer = InMemorySaver()
_agent = None
_agent_lock = asyncio.Lock()
_tool_source = "none"


# ---------------------------------------------------------------- building

async def _load_tools():
    """Tools from the MCP server, or the local functions as a fallback."""
    global _tool_source
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
        client = MultiServerMCPClient({"document": {
            "url": settings.mcp_url, "transport": "streamable_http"}})
        loaded = await client.get_tools()
        _tool_source = "mcp"
        return loaded
    except Exception as e:  # noqa: BLE001 - any failure → fallback
        print(f"[agent] MCP not reachable ({e}); using local tools.")
        _tool_source = "local"
        return [StructuredTool.from_function(fn) for fn in local_tools.ALL_TOOLS]


def _trim_history(state: dict) -> dict:
    """Runs before every model call: keep the newest messages within budget."""
    trimmed = trim_messages(
        state["messages"],
        max_tokens=settings.memory_max_tokens,
        token_counter=count_tokens_approximately,
        strategy="last",          # keep the most recent messages
        start_on="human",         # never start in the middle of a tool exchange
        include_system=True,
    )
    return {"llm_input_messages": _show_current_photo(trimmed)}


PHOTO_MARK = re.compile(r"\[The user attached a photo: id = (upload_\w+)\]")


def _show_current_photo(messages: list) -> list:
    """Put the user's photo inside the LAST user message, so the VLM sees it
    directly. Only in what is sent to the model: the memory keeps the short
    text id, not the heavy image (older photos are not sent again)."""
    humans = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
    if not humans:
        return messages
    i = humans[-1]
    text = str(messages[i].content)
    match = PHOTO_MARK.search(text)
    path = find_image_file(match.group(1)) if match else None
    if path is None:
        return messages
    with_image = HumanMessage(content=[
        {"type": "text", "text": text},
        {"type": "image_url",
         "image_url": f"data:image/jpeg;base64,{to_base64(path)}"},
    ])
    return messages[:i] + [with_image] + messages[i + 1:]


async def get_agent():
    global _agent
    async with _agent_lock:
        if _agent is None:
            model = ChatOllama(model=settings.ollama_vlm_model,
                               base_url=settings.ollama_url, temperature=0.2,
                               num_ctx=settings.ollama_num_ctx,
                               num_predict=settings.ollama_num_predict)
            _agent = create_react_agent(
                model,
                tools=await _load_tools(),
                prompt=SYSTEM_PROMPT,
                checkpointer=_checkpointer,
                pre_model_hook=_trim_history,
            )
    return _agent


def tool_source() -> str:
    """'mcp', 'local' or 'none' (agent not built yet) — shown by /health."""
    return _tool_source


# ---------------------------------------------------------------- reading results

def _tool_text(message: ToolMessage) -> str:
    """Tool results can be a string or a list of content blocks (MCP)."""
    content = message.content
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b)
                       for b in content)
    return str(content)


def summarize_turn(messages: list) -> dict:
    """From the messages of one turn: tool calls, cited pages, found images."""
    steps, pages, images = [], [], []
    for m in messages:
        if isinstance(m, AIMessage):
            for call in m.tool_calls:
                steps.append({"tool": call["name"], "args": call["args"]})
        elif isinstance(m, ToolMessage):
            try:
                data = json.loads(_tool_text(m))
            except (json.JSONDecodeError, TypeError):
                continue
            pages += [p for p in data.get("pages", []) if p not in pages]
            for img in data.get("images", []):
                threshold = (settings.image_min_similarity_photo
                             if m.name == "identify_image"
                             else settings.image_min_similarity_text)
                if img.get("similarity", 0) >= threshold and \
                        all(i["image_id"] != img["image_id"] for i in images):
                    images.append({k: img[k] for k in
                                   ("image_id", "url", "page", "similarity")})
    return {"steps": steps, "pages": pages, "images": images}


# ---------------------------------------------------------------- memory

HISTORY_MAX_MESSAGES = 20   # saved messages used to rebuild a lost memory


def history_messages(history: list[dict] | None) -> list:
    """Saved messages [{role, content}] → LangChain messages."""
    return [HumanMessage(h["content"]) if h["role"] == "user" else AIMessage(h["content"])
            for h in (history or [])[-HISTORY_MAX_MESSAGES:] if h.get("content")]


async def restore_memory(graph, conversation_id: str,
                         history: list[dict] | None) -> list:
    """The app saves every chat in Supabase and sends its past messages.
    The memory here is in RAM: after a restart of the backend, or for a chat
    reopened from the history, it is empty. Then the saved messages are put
    back before the new question. If the memory is still there, nothing."""
    if not history:
        return []
    state = await graph.aget_state({"configurable": {"thread_id": conversation_id}})
    if state.values.get("messages"):
        return []
    return history_messages(history)


# ---------------------------------------------------------------- running

async def run(question: str, conversation_id: str = "default",
              photo: Image.Image | None = None,
              history: list[dict] | None = None) -> dict:
    start = time.perf_counter()
    agent = await get_agent()

    text = question
    if photo is not None:
        # The photo is stored on disk and referred to by its id. The model
        # sees the image itself through _show_current_photo().
        upload_id = await asyncio.to_thread(save_upload, photo)
        text += f"\n\n[The user attached a photo: id = {upload_id}]"

    config = {"configurable": {"thread_id": conversation_id},
              "recursion_limit": 16}   # max ~7 tool calls per question
    past = await restore_memory(agent, conversation_id, history)
    result = await agent.ainvoke({"messages": past + [HumanMessage(text)]}, config)

    # Only this turn: everything after the last user message.
    messages = result["messages"]
    last_human = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
    turn = summarize_turn(messages[last_human + 1:])

    sources = format_sources([{"source": "document", "page": p}
                              for p in turn["pages"]])
    return {
        "mode": "agent",
        "conversation_id": conversation_id,
        "answer": str(messages[-1].content).strip(),
        "sources": sources,
        "images": turn["images"],
        "steps": turn["steps"],
        "seconds": round(time.perf_counter() - start, 2),
    }


def reset(conversation_id: str) -> None:
    """Forget a conversation."""
    _checkpointer.delete_thread(conversation_id)
