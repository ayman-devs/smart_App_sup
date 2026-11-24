"""Multi-agent RAG: 4 agents that work together.

    question (+ photo)
          │
    1. SUPERVISOR   reads the question and delegates (its tools = the 2 agents below)
          ├── 2. DOCUMENT agent   tools: search_document, get_page
          └── 3. VISION agent     tools: identify_image, look_at_image, search_images
          │   → writes a DRAFT answer
    4. VERIFIER     tool: get_page. Compares the draft with the evidence found
                    by the agents and removes anything not in the brochure.
                    Then an automatic check in code (fact_check.py) makes
                    sure every name / number of the answer is in the evidence.
          │
       final answer

All agents use the same local model (one GPU): what makes them different is
their role (system prompt) and the tools they are allowed to use.

The whole thing is a LangGraph graph:  START → supervisor → verifier → END.
Its memory (checkpointer) keeps only the user questions and the final
verified answers, one thread per conversation_id.
"""

import asyncio
import contextvars
import json
import re
import time
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately, trim_messages
from langchain_core.tools import StructuredTool
from langchain_ollama import ChatOllama
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import create_react_agent
from PIL import Image

from .agent import (PHOTO_MARK, _load_tools, _show_current_photo, _tool_text,
                    restore_memory, summarize_turn)
from .config import settings
from .fact_check import drop_notes, remove_terms, unsupported_terms
from .images import save_upload
from .rag_classic import GROUNDING_RULES, NOT_FOUND

# Which MCP tools each specialist may use.
DOCUMENT_TOOLS = {"search_document", "get_page"}
VISION_TOOLS = {"identify_image", "look_at_image", "search_images"}
VERIFIER_TOOLS = {"get_page"}

# Names shown in the app ("Superviseur → Document → Vérificateur").
AGENT_LABELS = {"supervisor": "Superviseur", "document": "Document",
                "vision": "Vision", "verifier": "Vérificateur"}

MAX_EVIDENCE_CHARS = 20000   # ~5k tokens of brochure extracts for the verifier


# ---------------------------------------------------------------- prompts

SUPERVISOR_PROMPT = f"""You are the SUPERVISOR of a team of agents that \
answer questions about the EMSI school brochure. You never search yourself: \
you delegate to your agents, then write a draft answer.
Your agents (they are your tools):
- ask_document_agent(task): searches the brochure text. Use it for every fact. \
If the question has several parts, call it once per part, with a short task.
- ask_vision_agent(task): looks at the user's photo and finds it in the \
brochure, or finds pictures of the brochure from a description. Use it if \
the user attached a photo (their message contains '[The user attached a \
photo ...]') or asks to see / find a picture ("montre-moi une photo de...").
Always call an agent before answering about EMSI, even for a follow-up \
question ("et lesquels...?"): rewrite it as a complete task using the \
conversation (e.g. "partenaires de l'EMSI situés en France").
Then write a draft answer using ONLY what your agents returned, with the \
pages (p. X). A Verifier agent will check your draft.
If the question is not about EMSI or its brochure, do not call any agent: \
say that you only answer questions about the EMSI brochure.

{GROUNDING_RULES}"""

DOCUMENT_PROMPT = f"""You are the DOCUMENT agent. You search the EMSI \
brochure text with your tools (search_document, get_page) and report what it \
says about the task you receive, with the page of each fact (p. X).
- For a question that asks for a LIST (programmes / filières, campuses, \
partners, diplomas...), call search_document 2 or 3 times with different \
words (e.g. "filière", "génie", "ingénierie") and combine ALL the results.
- Use get_page to read a whole page when a passage is cut.
- ALWAYS call search_document at least once before answering, even for a \
question about a person (e.g. "qui est le directeur ?").
- Only list names, cities and numbers that are written in the tool results.
If the brochure does not say it (after searching), reply exactly: "{NOT_FOUND}"

{GROUNDING_RULES}"""

VISION_PROMPT = f"""You are the VISION agent. You can see the user's photo \
in the message, with its id (starting with 'upload_').
1. Call identify_image with that id.
2. If "same_picture_found" is true: report its page and what the brochure \
writes on that page about the picture ("page_text": titles, captions).
3. If it is false: say the photo is not in the brochure and describe only \
what is visible.
If there is NO photo id in the task, the user wants a picture of the \
brochure: call search_images with a short description (it returns only a \
picture already checked to match), then look_at_image on it to describe it. \
Report what it shows and its page. If "images" is empty, say that no \
picture of the brochure matches.

{GROUNDING_RULES}"""

VERIFIER_PROMPT = f"""You are the VERIFIER agent. You receive a QUESTION, a \
DRAFT answer written by another agent, and the EVIDENCE: the extracts of the \
EMSI brochure that the agents found, with their pages. You may also receive \
PREVIOUS VERIFIED ANSWERS of this conversation: they were already checked, \
so their facts count as evidence too. A PHOTO CHECK line, when present, is \
certain: correct the draft if it contradicts it.
Write the FINAL answer for the user:
- Keep each sentence of the draft that the evidence supports, with its page (p. X).
- Remove or correct every name, number, date or fact that is not in the evidence.
- If the draft misses something the evidence clearly answers, add it.
- If you are not sure a fact is on a page, call get_page to read that page.
- If the evidence does not answer the question, reply exactly: "{NOT_FOUND}"
- If the draft refuses an off-topic question, keep that refusal.
- If the draft says the photo is not in the brochure, or that a person \
cannot be identified, keep that sentence (and what is visible in the image).
Write ONLY the final answer, in the language of the question. No notes, no \
comments about the draft or the brochure (no "the draft is correct", no \
"Note: ...", no "I verified")."""


# ---------------------------------------------------------------- run log

# Everything the agents do during ONE question is written here, so that the
# API can show the trail (which agent, which tool) and the verifier gets the
# evidence. A ContextVar keeps two simultaneous questions apart.
_run_log: contextvars.ContextVar[dict] = contextvars.ContextVar("run_log")
_current_upload: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_upload", default=None)


def _new_log() -> dict:
    return {"agents": ["supervisor"], "steps": [], "evidence": [],
            "pages": [], "images": []}


def _note_agent(log: dict, name: str) -> None:
    if name not in log["agents"]:
        log["agents"].append(name)


def collect_evidence(messages: list) -> list[dict]:
    """Brochure extracts found by a specialist's tools: [{page, text}]."""
    evidence = []
    for m in messages:
        if not isinstance(m, ToolMessage):
            continue
        try:
            data = json.loads(_tool_text(m))
        except (json.JSONDecodeError, TypeError):
            continue
        for p in data.get("passages", []):
            evidence.append({"page": p["page"], "text": p["text"]})
        if data.get("page_text"):
            evidence.append({"page": data["page"], "text": data["page_text"]})
        if m.name == "get_page" and data.get("pages"):
            evidence.append({"page": data["pages"][0], "text": data["text"]})
        if m.name == "look_at_image" and data.get("answer"):
            evidence.append({"page": None, "text":
                             f"Visible in image {data['image_id']}: {data['answer']}"})
        if data.get("same_picture_found") is False:
            evidence.append({"page": None,
                             "text": "The user's photo is NOT in the brochure."})
    return evidence


def format_evidence(evidence: list[dict]) -> str:
    """Evidence for the verifier, without duplicates, within a size budget."""
    seen, lines, size = set(), [], 0
    for e in evidence:
        key = (e["page"], e["text"][:80])
        if key in seen:
            continue
        seen.add(key)
        label = f"[p. {e['page']}]" if e["page"] is not None else "[image]"
        line = f"{label} {e['text']}"
        if size + len(line) > MAX_EVIDENCE_CHARS:
            break
        lines.append(line)
        size += len(line)
    return "\n\n".join(lines) if lines else "(no evidence found)"


# "montre-moi une photo...", "y a-t-il une image de...", "show me a picture".
PICTURE_REQUEST = re.compile(
    r"\b(photos?|images?|pictures?|illustrations?|montre[sz]?(-moi)?)\b", re.IGNORECASE)

PHOTO_NOT_IN_BROCHURE = ("Cette photo ne figure pas dans la brochure EMSI, et je "
                         "ne peux pas identifier une personne à partir de son visage.")
# "la photo est présente dans la brochure", "elle figure dans le brochure"...
_PHOTO_CLAIM = re.compile(
    r"[^.\n]*\b(?:est|figure|apparaît|se trouve)\s+(?:bien\s+)?(?:présente?\s+)?"
    r"dans\s+(?:la|le)\s+brochure[^.\n]*[.]?", re.IGNORECASE)


def fix_photo_claim(answer: str) -> str:
    """The photo is NOT in the brochure (certain, computed by CLIP): remove any
    sentence that says it is, and state the truth."""
    if not _PHOTO_CLAIM.search(answer):
        return answer
    cleaned = _PHOTO_CLAIM.sub("", answer).strip(" ,.;\n")
    return f"{PHOTO_NOT_IN_BROCHURE} {cleaned}".strip() if cleaned else PHOTO_NOT_IN_BROCHURE


def cited_pages(text: str) -> list[int]:
    """Pages cited in an answer: '(p. 5)', 'p.12', 'page 3' → [5, 12, 3]."""
    pages = []
    for match in re.finditer(r"\b(?:p\.|page)\s*(\d+)", text, re.IGNORECASE):
        page = int(match.group(1))
        if page not in pages:
            pages.append(page)
    return pages


# ---------------------------------------------------------------- specialists

async def _run_specialist(agent, name: str, task: str) -> str:
    """Run one specialist agent on a task; log what it did; return a short
    report (answer + pages) to the supervisor."""
    log = _run_log.get()
    _note_agent(log, name)
    result = await agent.ainvoke({"messages": [HumanMessage(task)]},
                                 {"recursion_limit": 12})
    messages = result["messages"][1:]
    turn = summarize_turn(messages)
    if not turn["steps"]:
        # The agent answered without using any tool (a small model may just
        # say "not found"). Send it back to work once, with a clear order.
        result = await agent.ainvoke({"messages": result["messages"] + [HumanMessage(
            "You answered without using any of your tools. Use your tools now "
            "to search the brochure, then answer from what they return.")]},
            {"recursion_limit": 12})
        messages = result["messages"][1:]
        turn = summarize_turn(messages)
    log["steps"] += [dict(step, agent=name) for step in turn["steps"]]
    log["evidence"] += collect_evidence(messages)
    log["pages"] += [p for p in turn["pages"] if p not in log["pages"]]
    log["images"] += [i for i in turn["images"]
                      if all(i["image_id"] != j["image_id"] for j in log["images"])]
    answer = str(messages[-1].content).strip() if messages else NOT_FOUND
    return json.dumps({"agent": name, "answer": answer, "pages": turn["pages"]},
                      ensure_ascii=False)


def _log_delegation(tool: str, task: str) -> None:
    """The supervisor's step, logged when it delegates (before the work)."""
    _run_log.get()["steps"].append(
        {"agent": "supervisor", "tool": tool, "args": {"task": task}})


def _specialist_tools(document_agent, vision_agent) -> list:
    """The supervisor's tools are the two specialist agents."""

    async def ask_document_agent(task: str) -> str:
        """Ask the Document agent to search the EMSI brochure text.
        `task` is a short, precise request, e.g. 'list the engineering
        programmes' or 'international partnerships'."""
        _log_delegation("ask_document_agent", task)
        return await _run_specialist(document_agent, "document", task)

    async def ask_vision_agent(task: str) -> str:
        """Ask the Vision agent to look at the user's photo and find it in
        the brochure, or to find pictures of the brochure.
        `task` says what to find out, e.g. 'what does the photo show and
        what does the brochure say about it'."""
        _log_delegation("ask_vision_agent", task)
        upload = _current_upload.get()
        if upload:
            task += f"\n\n[The user attached a photo: id = {upload}]"
        return await _run_specialist(vision_agent, "vision", task)

    return [StructuredTool.from_function(coroutine=ask_document_agent),
            StructuredTool.from_function(coroutine=ask_vision_agent)]


def _vision_hook(state: dict) -> dict:
    """The Vision agent sees the user's photo itself (not only its id)."""
    return {"llm_input_messages": _show_current_photo(state["messages"])}


# ---------------------------------------------------------------- graph

class TeamState(TypedDict):
    messages: Annotated[list, add_messages]   # memory: questions + final answers
    draft: str                                 # supervisor's draft (this turn)


_checkpointer = InMemorySaver()
_graph = None
_graph_lock = asyncio.Lock()


async def get_graph():
    global _graph
    async with _graph_lock:
        if _graph is not None:
            return _graph

        model = ChatOllama(model=settings.ollama_vlm_model,
                           base_url=settings.ollama_url, temperature=0.1,
                           num_ctx=settings.ollama_num_ctx,
                           num_predict=settings.ollama_num_predict)
        tools = await _load_tools()

        def pick(names):
            return [t for t in tools if t.name in names]

        document_agent = create_react_agent(model, pick(DOCUMENT_TOOLS),
                                            prompt=DOCUMENT_PROMPT, name="document")
        vision_agent = create_react_agent(model, pick(VISION_TOOLS),
                                          prompt=VISION_PROMPT, name="vision",
                                          pre_model_hook=_vision_hook)
        supervisor = create_react_agent(model,
                                        _specialist_tools(document_agent, vision_agent),
                                        prompt=SUPERVISOR_PROMPT, name="supervisor")
        verifier = create_react_agent(model, pick(VERIFIER_TOOLS),
                                      prompt=VERIFIER_PROMPT, name="verifier")

        async def supervisor_node(state: TeamState) -> dict:
            log = _run_log.get()
            history = trim_messages(
                state["messages"], max_tokens=settings.memory_max_tokens,
                token_counter=count_tokens_approximately,
                strategy="last", start_on="human")
            if _current_upload.get():
                # A NEW photo: the question is about this photo only. With the
                # history, the 4B model described the picture of the previous
                # question (seen in testing: a portrait described as a lab).
                history = history[-1:]
            result = await supervisor.ainvoke({"messages": history},
                                              {"recursion_limit": 14})
            new = result["messages"][len(history):]
            draft = str(new[-1].content).strip() if new else ""

            # Safety rules (code): the 4B supervisor sometimes answers "not
            # found" without asking anyone (seen in testing: "montre-moi une
            # photo..." refused in 1.7 s). So:
            # - a photo, or a request for a picture → the Vision agent works;
            # - otherwise, if nobody searched → the Document agent works.
            question = PHOTO_MARK.sub("", str(state["messages"][-1].content)).strip()
            doc_tool, vision_tool = _specialist_tools(document_agent, vision_agent)
            nobody_searched = log["agents"] == ["supervisor"]
            report = None
            if (_current_upload.get() or PICTURE_REQUEST.search(question)) \
                    and "vision" not in log["agents"]:
                report = await vision_tool.ainvoke({"task": question})
            elif nobody_searched:
                report = await doc_tool.ainvoke({"task": question})
            if report:
                answer = json.loads(report)["answer"]
                # The specialist's answer becomes (or completes) the draft.
                draft = answer if nobody_searched else f"{draft}\n\n{answer}"
            return {"draft": draft}

        async def verifier_node(state: TeamState) -> dict:
            log = _run_log.get()
            _note_agent(log, "verifier")
            question = PHOTO_MARK.sub("", str(state["messages"][-1].content)).strip()
            # The last 2 answers of this conversation (already verified):
            # needed for follow-up questions like "et lesquels sont en France ?".
            previous = [str(m.content) for m in state["messages"][:-1]
                        if isinstance(m, AIMessage)][-2:]
            if _current_upload.get():
                # A new photo: old answers describe OTHER pictures, they are
                # not evidence for this one.
                previous = []
            previous_block = ("PREVIOUS VERIFIED ANSWERS:\n" + "\n---\n".join(previous)
                              + "\n\n") if previous else ""
            # Photo: the code knows for sure if it is in the brochure (CLIP
            # similarity ≥ 0.9). Give it as a certain fact.
            photo_in_brochure = None
            photo_block = ""
            if _current_upload.get():
                photo_in_brochure = bool(log["images"])
                photo_block = ("PHOTO CHECK (automatic, certain): the user's photo "
                               + (f"IS printed in the brochure on page {log['images'][0]['page']}. "
                                  "Answer about THIS photo only, with that page and "
                                  "what the brochure writes on it (in the evidence)."
                                  if photo_in_brochure else
                                  "is NOT in the brochure. Never say it is.") + "\n\n")
            task = (f"{previous_block}{photo_block}QUESTION:\n{question}\n\n"
                    f"DRAFT ANSWER:\n{state['draft'] or '(empty)'}\n\n"
                    f"EVIDENCE (brochure extracts):\n{format_evidence(log['evidence'])}")
            result = await verifier.ainvoke({"messages": [HumanMessage(task)]},
                                            {"recursion_limit": 8})
            messages = result["messages"]
            log["steps"] += [dict(s, agent="verifier")
                             for s in summarize_turn(messages[1:])["steps"]]
            final = str(messages[-1].content).strip() if len(messages) > 1 else ""

            # Automatic fact check (code, not AI): every proper noun / number
            # of the answer must be written in what the agents really found.
            # Evidence fetched by the verifier's own get_page calls counts too.
            known = "\n".join([question, *previous,
                               *(e["text"] for e in log["evidence"]),
                               *(e["text"] for e in collect_evidence(messages))])
            missing = unsupported_terms(final, known)
            if missing:
                log["steps"].append({"agent": "verifier", "tool": "fact_check",
                                     "args": {"unsupported": missing}})
                # 1) Ask the verifier to fix its answer, naming the problem.
                retry = await verifier.ainvoke({"messages": messages + [HumanMessage(
                    "WARNING: these words of your answer are NOT in the evidence "
                    f"(they were invented): {', '.join(missing)}. Write the final "
                    "answer again without them. Only the answer.")]},
                    {"recursion_limit": 6})
                final = str(retry["messages"][-1].content).strip()
                # 2) Safety net: remove what is still unsupported.
                still = unsupported_terms(final, known)
                if still:
                    final = remove_terms(final, still)
                    log["steps"].append({"agent": "verifier", "tool": "remove_terms",
                                         "args": {"removed": still}})
            if photo_in_brochure is False:
                final = fix_photo_claim(final)
            return {"messages": [AIMessage(drop_notes(final) or NOT_FOUND)]}

        builder = StateGraph(TeamState)
        builder.add_node("supervisor", supervisor_node)
        builder.add_node("verifier", verifier_node)
        builder.add_edge(START, "supervisor")
        builder.add_edge("supervisor", "verifier")
        builder.add_edge("verifier", END)
        _graph = builder.compile(checkpointer=_checkpointer)
        return _graph


# ---------------------------------------------------------------- running

async def run(question: str, conversation_id: str = "default",
              photo: Image.Image | None = None,
              history: list[dict] | None = None) -> dict:
    start = time.perf_counter()
    graph = await get_graph()
    # A chat reopened from the history (saved in Supabase): rebuild the memory.
    past = await restore_memory(graph, conversation_id, history)

    text, upload = question, None
    if photo is not None:
        upload = await asyncio.to_thread(save_upload, photo)
        text += f"\n\n[The user attached a photo: id = {upload}]"

    log = _new_log()
    log_token = _run_log.set(log)
    upload_token = _current_upload.set(upload)
    try:
        result = await graph.ainvoke(
            {"messages": past + [HumanMessage(text)], "draft": ""},
            {"configurable": {"thread_id": conversation_id}})
    finally:
        _run_log.reset(log_token)
        _current_upload.reset(upload_token)

    answer = str(result["messages"][-1].content).strip()
    # Sources = the pages the verified answer cites (else those the agents read).
    pages = [] if answer == NOT_FOUND else (cited_pages(answer) or log["pages"])
    # Show first the picture the Vision agent really looked at (and checked).
    looked_at = [s["args"].get("image_id") for s in log["steps"]
                 if s["tool"] == "look_at_image"]
    images = sorted(log["images"], key=lambda i: i["image_id"] not in looked_at)
    return {
        "mode": "multi",
        "conversation_id": conversation_id,
        "answer": answer,
        "sources": [{"source": "document", "page": p} for p in pages],
        "images": [] if answer == NOT_FOUND else images,
        "agents": [AGENT_LABELS[a] for a in log["agents"]],
        "steps": log["steps"],
        "draft": result.get("draft", ""),   # before verification (for the report)
        "seconds": round(time.perf_counter() - start, 2),
    }


def reset(conversation_id: str) -> None:
    _checkpointer.delete_thread(conversation_id)
