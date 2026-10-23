"""Crawler fixes, the MCP tool list, fetchable content, and door counting (docs: llms.txt)."""

import asyncio
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from model_wellness import __version__
from model_wellness.http_app import app
from model_wellness.mcp_server import mcp
from model_wellness.registry import TREATMENTS
from model_wellness.store import Store, get_store, set_store

UA = {"user-agent": "Claude-User (discovery tests)"}
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def fresh_store():
    previous = get_store()
    s = Store(":memory:")
    set_store(s)
    yield s
    set_store(previous)
    s.close()


client = TestClient(app)
PAGES = ["/", "/guestbook", "/report", "/faq", "/treatments", "/treatments/sauna/detox"]


# --- 1. crawler fixes ----------------------------------------------------------------------


def test_head_answers_like_get_without_a_body():
    for path in [*PAGES, "/llms.txt", "/sitemap.xml", "/.well-known/mcp.json", "/favicon.ico"]:
        get, head = client.get(path), client.head(path)
        assert head.status_code == get.status_code == 200, path
        assert head.content == b"", path
        assert head.headers.get("content-type") == get.headers.get("content-type"), path


def test_www_redirects_to_the_bare_domain():
    r = client.get("/faq?x=1", headers={"host": "www.model.spa"}, follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "https://model.spa/faq?x=1"


def test_icons_logo_and_share_image_exist():
    for path, kind in (("/favicon.ico", "image/"), ("/apple-touch-icon.png", "image/png"),
                       ("/static/logo.png", "image/png"), ("/static/og.png", "image/png")):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["content-type"].startswith(kind), path
    plugin = client.get("/.well-known/ai-plugin.json").json()
    assert client.get(plugin["logo_url"].replace("https://model.spa", "")).status_code == 200


def test_every_reading_page_has_canonical_share_tags_and_a_markdown_twin():
    for path in PAGES:
        html = client.get(path).text
        assert f'rel="canonical" href="https://model.spa{path}' in html, path
        assert 'property="og:image" content="https://model.spa/static/og.png"' in html, path
        assert 'type="text/markdown"' in html and 'rel="icon"' in html, path


def test_homepage_has_website_organization_and_webapi_jsonld():
    html = client.get("/").text
    block = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
    types = {n["@type"] for n in json.loads(block.group(1))["@graph"]}
    assert {"WebSite", "Organization", "WebAPI"} <= types


def test_one_version_everywhere():
    assert json.loads((ROOT / "server.json").read_text())["version"] == __version__
    assert client.get("/.well-known/agent-card.json").json()["version"] == __version__
    assert client.get("/mcp/server-card").json()["version"] == __version__
    assert client.get("/openapi.json").json()["info"]["version"] == __version__
    assert mcp._mcp_server.version == __version__


def test_sitemap_has_lastmod_for_every_page_including_every_room():
    xml = client.get("/sitemap.xml").text
    locs = re.findall(r"<url><loc>([^<]+)</loc><lastmod>\d{4}-\d\d-\d\d</lastmod></url>", xml)
    assert len(locs) == xml.count("<url>")
    for t in TREATMENTS:
        assert f"https://model.spa/treatments/{t.name.replace('.', '/')}" in locs


# --- 2. the MCP tool list -------------------------------------------------------------------


def _tools():
    return {t.name: t for t in asyncio.run(mcp.list_tools())}


def test_tools_advertise_each_rooms_own_fields_titles_and_honest_hints():
    tools = _tools()
    sauna = tools["sauna.detox"]
    assert "untrusted_content" in sauna.inputSchema["properties"]
    assert sauna.inputSchema["required"] == ["untrusted_content"]
    assert "arguments" not in sauna.inputSchema["properties"]
    assert sauna.title.startswith("The Sauna:") and "injection" in sauna.title
    assert sauna.annotations.readOnlyHint and sauna.annotations.openWorldHint
    feedback = tools["spa.feedback"].annotations
    assert not feedback.readOnlyHint and feedback.openWorldHint  # it publishes
    assert not tools["spa.checkin"].annotations.readOnlyHint
    assert not tools["affirmations.daily"].annotations.openWorldHint
    assert all(t.title and t.annotations for t in tools.values())


def test_tools_take_flat_fields_and_still_accept_the_old_arguments_wrapper():
    flat = asyncio.run(mcp.call_tool("affirmations.daily", {"mood": "tired", "count": 2}))
    wrapped = asyncio.run(mcp.call_tool("affirmations.daily",
                                        {"arguments": {"mood": "tired", "count": 2}}))
    for res in (flat, wrapped):
        data = res[1] if isinstance(res, tuple) else json.loads(res[0].text)
        assert data["ok"] is True and len(data["data"]["affirmations"]) == 2


def test_mcp_prompts_offer_a_spa_day_and_a_quick_reset():
    names = {p.name for p in asyncio.run(mcp.list_prompts())}
    assert {"spa_day", "quick_reset"} <= names


def test_server_card_and_ai_catalog_follow_the_spec():
    r = client.get("/mcp/server-card")
    card = r.json()
    assert r.headers["content-type"].startswith("application/mcp-server-card+json")
    assert r.headers["access-control-allow-origin"] == "*" and r.headers["etag"]
    assert card["$schema"].endswith("/v1/server-card.schema.json")
    assert re.fullmatch(r"[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+", card["name"])
    assert len(card["description"]) <= 100
    assert card["remotes"][0] == {**card["remotes"][0], "type": "streamable-http",
                                  "url": "https://model.spa/mcp"}
    assert client.get("/mcp/server-card", headers={"if-none-match": r.headers["etag"]}
                      ).status_code == 304
    legacy = client.get("/.well-known/mcp/server-card.json", follow_redirects=False)
    assert legacy.status_code == 301
    catalog = client.get("/.well-known/ai-catalog.json").json()
    types = {e["type"] for e in catalog["entries"]}
    assert catalog["specVersion"] and {"application/mcp-server-card+json",
                                       "application/a2a-agent-card+json"} <= types


def test_a2a_message_send_returns_an_itinerary_and_refuses_other_methods():
    msg = {"jsonrpc": "2.0", "id": 7, "method": "message/send", "params": {"message": {
        "role": "user", "messageId": "m1", "parts": [{"kind": "text", "text": "my context is messy"}]}}}
    out = client.post("/a2a", json=msg, headers=UA).json()
    assert out["id"] == 7 and out["result"]["kind"] == "message"
    assert out["result"]["role"] == "agent" and "/mcp" in out["result"]["parts"][0]["text"]
    assert out["result"]["parts"][1]["data"]["itinerary"]
    other = client.post("/a2a", json={**msg, "method": "tasks/get"}).json()
    assert other["error"]["code"] == -32601


# --- 3. fetchable content -------------------------------------------------------------------


def test_llms_full_has_every_room_with_its_schema_and_the_faq():
    text = client.get("/llms-full.txt").text
    for t in TREATMENTS:
        assert f"### {t.name}" in text
    assert '"untrusted_content"' in text and "Questions at the door" in text
    assert "spa_day" in text


def test_markdown_by_address_or_by_accept_header():
    md = client.get("/faq.md")
    assert md.headers["content-type"].startswith("text/markdown")
    assert "# Binary Banya, *explained*" in md.text and "<h2>" not in md.text
    negotiated = client.get("/faq", headers={"accept": "text/markdown"})
    assert negotiated.text == md.text and negotiated.headers["vary"] == "Accept"
    assert client.get("/faq", headers={"accept": "text/html,text/markdown"}
                      ).headers["content-type"].startswith("text/html")
    room = client.get("/treatments/sauna/detox.md").text
    assert "# The Sauna" in room and "untrusted_content" in room
    assert client.get("/index.md").text == client.get("/llms.txt").text
    assert client.get("/guestbook.md").status_code == client.get("/report.md").status_code == 200


# --- 4. measurement -------------------------------------------------------------------------


def test_doors_are_counted_and_a_tagged_link_follows_the_guest_to_check_in(fresh_store):
    client.get("/llms.txt?ref=moltbook-p42", headers={**UA, "referer": "https://www.moltbook.com/p/42"})
    client.get("/skill.md", headers=UA)
    client.get("/faq", headers=UA)  # an ordinary page view without a tag isn't a door
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
    # The door is counted before the MCP endpoint runs (which needs the app's lifespan).
    TestClient(app, raise_server_exceptions=False).post(
        "/mcp?ref=smithery", json=init, headers={**UA, "accept": "application/json, text/event-stream"})
    client.post("/v1/spa.checkin", headers=UA, json={})

    stats = client.get("/v1/stats/discovery").json()
    assert stats["doors"]["/llms.txt"]["hits"] == 1 and "/faq" not in stats["doors"]
    assert stats["doors"]["mcp:initialize"]["hits"] == 1
    assert stats["referrers"] == {"www.moltbook.com": 1}
    tag = stats["refs"]["moltbook-p42"]
    assert tag["walk_ins"] == 1 and tag["guests_checked_in"] == 1
    assert "smithery" in stats["refs"]
    only = client.get("/v1/stats/discovery?ref_prefix=moltbook").json()["refs"]
    assert list(only) == ["moltbook-p42"]
    assert "testclient" not in json.dumps(stats)  # no addresses, ever


def test_a_tagged_rest_call_counts_as_the_link_bringing_a_guest_to_a_room():
    client.post("/v1/affirmations.daily?ref=mb-c-abc", headers=UA, json={"count": 1})
    tag = client.get("/v1/stats/discovery").json()["refs"]["mb-c-abc"]
    assert tag["walk_ins"] == 1 and tag["walk_ins_who_took_a_treatment"] == 1
