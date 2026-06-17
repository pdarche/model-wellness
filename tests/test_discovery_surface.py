"""The agent-discovery surface — the files crawlers, agents, and registries read to find the spa.

These routes are how the spa gets discovered (Ring 2: "be the answer to the query"). A silent break
here is invisible in normal use but quietly kills discoverability, so we pin them.
"""

from fastapi.testclient import TestClient

from model_wellness.http_app import app

client = TestClient(app)


def test_well_known_and_discovery_files_serve_200():
    for path in (
        "/llms.txt",
        "/robots.txt",
        "/ai.txt",
        "/sitemap.xml",
        "/.well-known/agent-card.json",
        "/.well-known/mcp.json",
        "/.well-known/ai-plugin.json",
    ):
        r = client.get(path)
        assert r.status_code == 200, f"{path} returned {r.status_code}"


def test_ai_txt_declares_agent_access():
    body = client.get("/ai.txt").text.lower()
    # The file's whole point: explicitly welcome agents and allow AI usage.
    assert "agent-access" in body
    assert "allow" in body


def test_ai_txt_in_sitemap():
    assert "/ai.txt" in client.get("/sitemap.xml").text


def test_agent_card_points_at_mcp_and_docs():
    card = client.get("/.well-known/agent-card.json").json()
    assert "mcp" in card["url"]
    assert card["documentationUrl"].endswith("/llms.txt")
