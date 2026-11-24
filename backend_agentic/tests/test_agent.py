"""Agent helpers: reading tool calls, pages and images from one turn."""

import json

from langchain_core.messages import AIMessage, ToolMessage

from app.agent import summarize_turn


def test_summarize_turn_collects_steps_pages_and_good_images():
    messages = [
        AIMessage(content="", tool_calls=[
            {"name": "search_document", "args": {"query": "fees"}, "id": "1"},
            {"name": "identify_image", "args": {"upload_id": "upload_a"}, "id": "2"},
        ]),
        ToolMessage(json.dumps({"pages": [4, 5], "passages": []}),
                    tool_call_id="1", name="search_document"),
        ToolMessage(json.dumps({"pages": [7], "images": [
            {"image_id": "img_good", "url": "/images/a.png", "page": 7, "similarity": 0.91},
            {"image_id": "img_weak", "url": "/images/b.png", "page": 8, "similarity": 0.40},
        ]}), tool_call_id="2", name="identify_image"),
        AIMessage(content="Final answer (p. 4)"),
    ]
    turn = summarize_turn(messages)
    assert [s["tool"] for s in turn["steps"]] == ["search_document", "identify_image"]
    assert turn["pages"] == [4, 5, 7]
    # identify_image uses the strict "photo" threshold: 0.40 is rejected.
    assert [i["image_id"] for i in turn["images"]] == ["img_good"]


def test_summarize_turn_reads_mcp_content_blocks():
    block = [{"type": "text", "text": json.dumps({"pages": [2]})}]
    turn = summarize_turn([ToolMessage(block, tool_call_id="1", name="get_page")])
    assert turn["pages"] == [2]


def test_current_photo_is_shown_to_the_model_but_not_old_ones(env):
    from langchain_core.messages import HumanMessage
    from PIL import Image

    from app.agent import _show_current_photo
    from app.images import save_upload

    old_id = save_upload(Image.new("RGB", (32, 32), "blue"))
    new_id = save_upload(Image.new("RGB", (32, 32), "red"))
    messages = [
        HumanMessage(f"first\n\n[The user attached a photo: id = {old_id}]"),
        AIMessage(content="ok"),
        HumanMessage(f"second\n\n[The user attached a photo: id = {new_id}]"),
    ]
    sent = _show_current_photo(messages)
    assert isinstance(sent[0].content, str)          # old photo: text only
    blocks = sent[2].content                          # current photo: image added
    assert blocks[1]["type"] == "image_url"
    assert blocks[1]["image_url"].startswith("data:image/jpeg;base64,")
    assert isinstance(messages[2].content, str)       # memory itself unchanged


class _FakeState:
    def __init__(self, messages):
        self.values = {"messages": messages} if messages else {}


class _FakeGraph:
    """Only what restore_memory() uses: the saved state of a conversation."""

    def __init__(self, messages):
        self.messages = messages

    async def aget_state(self, config):
        return _FakeState(self.messages)


def test_saved_history_rebuilds_a_lost_memory():
    import asyncio
    from langchain_core.messages import AIMessage, HumanMessage
    from app.agent import restore_memory
    history = [{"role": "user", "content": "Quelles filières ?"},
               {"role": "assistant", "content": "Génie civil (p. 19)"}]
    # Memory empty (backend restarted, chat reopened): the history is restored.
    past = asyncio.run(restore_memory(_FakeGraph([]), "c1", history))
    assert [type(m) for m in past] == [HumanMessage, AIMessage]
    assert past[1].content == "Génie civil (p. 19)"
    # Memory still there: nothing is added twice.
    assert asyncio.run(restore_memory(_FakeGraph([HumanMessage("x")]), "c1", history)) == []
    assert asyncio.run(restore_memory(_FakeGraph([]), "c1", [])) == []


def test_api_passes_the_history_to_the_agent(env, monkeypatch):
    from fastapi.testclient import TestClient
    from app import agent, main
    received = {}

    async def fake_run(question, conversation_id, photo=None, history=None):
        received["history"] = history
        return {"mode": "agent", "answer": "ok", "sources": [], "images": [],
                "steps": [], "seconds": 0.1}

    monkeypatch.setattr(agent, "run", fake_run)
    with TestClient(main.app) as client:
        r = client.post("/chat", json={
            "question": "et en France ?", "mode": "agent", "conversation_id": "c1",
            "history": [{"role": "user", "content": "Partenaires ?"},
                        {"role": "assistant", "content": "ENSTA (p. 35)"}]})
    assert r.status_code == 200
    assert received["history"][1] == {"role": "assistant", "content": "ENSTA (p. 35)"}
