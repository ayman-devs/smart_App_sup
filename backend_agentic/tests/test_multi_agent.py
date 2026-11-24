"""Multi-agent mode: evidence, page citations, specialist logging, API."""

import asyncio
import json

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app import main, multi_agent
from app.multi_agent import (_new_log, _run_log, _run_specialist, cited_pages,
                             collect_evidence, format_evidence)
from app.rag_classic import NOT_FOUND


def _tool(name, data, call_id="1"):
    return ToolMessage(json.dumps(data), tool_call_id=call_id, name=name)


def test_collect_evidence_from_every_kind_of_tool():
    messages = [
        _tool("search_document", {"pages": [4], "passages": [{"page": 4, "text": "Génie civil"}]}),
        _tool("identify_image", {"same_picture_found": True, "page": 5, "pages": [5],
                                 "page_text": "LE MOT DE MOHAMMED EL RHABI"}),
        _tool("get_page", {"pages": [7], "text": "Page sept"}),
        _tool("look_at_image", {"image_id": "upload_x", "answer": "Un homme en costume"}),
        _tool("identify_image", {"same_picture_found": False, "pages": []}),
    ]
    evidence = collect_evidence(messages)
    pages = [e["page"] for e in evidence]
    assert pages == [4, 5, 7, None, None]
    assert "MOHAMMED EL RHABI" in evidence[1]["text"]
    assert "NOT in the brochure" in evidence[4]["text"]


def test_format_evidence_removes_duplicates_and_labels_pages():
    text = format_evidence([{"page": 3, "text": "A"}, {"page": 3, "text": "A"},
                            {"page": None, "text": "Visible: B"}])
    assert text.count("[p. 3] A") == 1
    assert "[image] Visible: B" in text
    assert format_evidence([]) == "(no evidence found)"


def test_cited_pages():
    assert cited_pages("Génie civil (p. 19), voir aussi page 4 et p.19") == [19, 4]
    assert cited_pages(NOT_FOUND) == []


class FakeSpecialist:
    """Stands for a LangGraph agent: one tool call, then an answer."""

    async def ainvoke(self, state, config=None):
        task = state["messages"][0]
        return {"messages": [
            task,
            AIMessage(content="", tool_calls=[
                {"name": "search_document", "args": {"query": "fees"}, "id": "1"}]),
            _tool("search_document", {"pages": [9], "passages": [{"page": 9, "text": "Frais"}]}),
            AIMessage(content="Les frais sont en page 9 (p. 9)."),
        ]}


def test_run_specialist_logs_steps_evidence_and_reports_to_supervisor():
    log = _new_log()
    token = _run_log.set(log)
    try:
        report = json.loads(asyncio.run(
            _run_specialist(FakeSpecialist(), "document", "frais ?")))
    finally:
        _run_log.reset(token)
    # The supervisor only gets a short report...
    assert report == {"agent": "document", "answer": "Les frais sont en page 9 (p. 9).",
                      "pages": [9]}
    # ...while the log keeps everything for the verifier and the app.
    assert log["agents"] == ["supervisor", "document"]
    assert log["steps"][0] == {"tool": "search_document", "args": {"query": "fees"},
                               "agent": "document"}
    assert log["evidence"] == [{"page": 9, "text": "Frais"}]


def test_api_multi_mode_and_reset(env, monkeypatch):
    async def fake_run(question, conversation_id, photo=None, history=None):
        return {"mode": "multi", "answer": "ok (p. 1)", "sources": [{"source": "document", "page": 1}],
                "images": [], "agents": ["Superviseur", "Document", "Vérificateur"],
                "steps": [], "draft": "ok", "seconds": 0.1}

    resets = []
    monkeypatch.setattr(multi_agent, "run", fake_run)
    monkeypatch.setattr(multi_agent, "reset", resets.append)
    with TestClient(main.app) as client:
        r = client.post("/chat", json={"question": "hi", "mode": "multi",
                                       "conversation_id": "c9"})
        assert r.status_code == 200
        assert r.json()["agents"] == ["Superviseur", "Document", "Vérificateur"]
        client.post("/agent/reset", json={"conversation_id": "c9"})
    assert resets == ["c9"]


def test_supervisor_history_question_strips_photo_mark():
    # The verifier must see the question without the technical photo id.
    from app.agent import PHOTO_MARK
    text = "Qui est-ce ?\n\n[The user attached a photo: id = upload_abc123]"
    assert PHOTO_MARK.sub("", text).strip() == "Qui est-ce ?"
    assert isinstance(HumanMessage(text).content, str)


def test_fix_photo_claim_corrects_a_false_claim():
    from app.multi_agent import PHOTO_NOT_IN_BROCHURE, fix_photo_claim
    # Real answer of the 4B model, while the photo was NOT in the brochure.
    wrong = ("Je ne peux pas identifier cette personne à partir de la photo. Je ne peux "
             "que vous dire que la photo est présente dans le brochure EMSI, mais je ne "
             "peux pas déterminer qui elle représente.")
    fixed = fix_photo_claim(wrong)
    assert fixed.startswith(PHOTO_NOT_IN_BROCHURE)
    assert "est présente" not in fixed


def test_fix_photo_claim_keeps_correct_answers():
    from app.multi_agent import fix_photo_claim
    right = "Cette photo n'est pas présente dans la brochure. Elle montre un homme."
    assert fix_photo_claim(right) == right


def test_picture_requests_are_detected():
    from app.multi_agent import PICTURE_REQUEST
    for q in ["Montre-moi une photo où des étudiants portent une blouse blanche",
              "Y a-t-il une image du campus ?", "show me a picture of the lab"]:
        assert PICTURE_REQUEST.search(q), q
    for q in ["Quelles filières propose l'EMSI ?", "Qui est le directeur ?"]:
        assert not PICTURE_REQUEST.search(q), q


class LazySpecialist:
    """First answers without any tool, then works when told to."""

    def __init__(self):
        self.calls = 0

    async def ainvoke(self, state, config=None):
        self.calls += 1
        if self.calls == 1:
            return {"messages": [state["messages"][0], AIMessage(content=NOT_FOUND)]}
        return await FakeSpecialist().ainvoke({"messages": state["messages"][:1]})


def test_specialist_that_skips_its_tools_is_sent_back_to_work():
    lazy = LazySpecialist()
    log = _new_log()
    token = _run_log.set(log)
    try:
        report = json.loads(asyncio.run(_run_specialist(lazy, "document", "frais ?")))
    finally:
        _run_log.reset(token)
    assert lazy.calls == 2
    assert report["pages"] == [9]
    assert log["steps"][0]["tool"] == "search_document"
