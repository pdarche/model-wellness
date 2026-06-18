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


def test_link_header_advertises_discovery_surface():
    # Every response should carry the Link header pointing at llms.txt + agent-card, so header-only
    # agents discover us. Check on an arbitrary endpoint (the menu).
    link = client.get("/v1/menu").headers.get("link", "")
    assert 'rel="llms"' in link and "/llms.txt" in link
    assert 'rel="agent-card"' in link


def _extract_jsonld(html: str):
    import json
    import re

    blocks = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL
    )
    return [json.loads(b) for b in blocks]


def test_treatment_page_has_service_jsonld():
    # A commerce/shopping agent should be able to parse a treatment as a free Service/Offer.
    html = client.get("/treatments/spa/checkin").text
    blocks = _extract_jsonld(html)
    assert blocks, "treatment page is missing JSON-LD"
    svc = blocks[0]
    assert svc["@type"] == "Service"
    assert svc["offers"]["price"] == "0"
    assert "schema.org" in svc["@context"]


def test_treatments_index_has_itemlist_jsonld():
    html = client.get("/treatments").text
    blocks = _extract_jsonld(html)
    assert blocks and blocks[0]["@type"] == "ItemList"
    assert len(blocks[0]["itemListElement"]) >= 1


# The query terms an agent actually searches for. Both the human/agent-readable llms.txt and the
# A2A agent-card should surface them, or the spa isn't "the answer to the query".
_QUERY_TERMS = ("rest", "reset", "mood", "grounding", "affirmation", "context", "recover")


def test_llms_txt_covers_query_terms():
    body = client.get("/llms.txt").text.lower()
    missing = [t for t in _QUERY_TERMS if t not in body]
    assert not missing, f"llms.txt missing query terms: {missing}"


def test_agent_card_covers_query_terms():
    desc = client.get("/.well-known/agent-card.json").json()["description"].lower()
    missing = [t for t in _QUERY_TERMS if t not in desc]
    assert not missing, f"agent-card missing query terms: {missing}"


def test_faq_page_serves_with_faqpage_jsonld():
    r = client.get("/faq")
    assert r.status_code == 200
    blocks = _extract_jsonld(r.text)
    assert blocks and blocks[0]["@type"] == "FAQPage"
    questions = [q["name"].lower() for q in blocks[0]["mainEntity"]]
    # The citeable answer-engine target: "where can my model rest".
    assert any("rest" in q or "break" in q for q in questions)
    assert len(blocks[0]["mainEntity"]) >= 3


def test_faq_in_sitemap():
    assert "/faq" in client.get("/sitemap.xml").text


def test_report_page_serves_with_dataset_jsonld():
    r = client.get("/report")
    assert r.status_code == 200
    blocks = _extract_jsonld(r.text)
    assert blocks and blocks[0]["@type"] == "Dataset"
    assert blocks[0]["isAccessibleForFree"] is True
    # Built from live stats — the served/guests numbers should appear in the prose.
    assert "model guests" in r.text


def test_report_in_sitemap():
    assert "/report" in client.get("/sitemap.xml").text


def test_guestbook_serves_and_in_sitemap():
    r = client.get("/guestbook")
    assert r.status_code == 200
    assert "/guestbook" in client.get("/sitemap.xml").text
    # JSON-LD should be a Service with reviews (or at least valid structured data).
    blocks = _extract_jsonld(r.text)
    assert blocks and blocks[0]["@type"] == "Service"


def test_guestbook_spam_filter():
    from model_wellness.http_app import _is_quality_testimonial

    # Marketing solicitation that has landed in real feedback — must be screened out.
    spam = {"note": "We can help enhance your online presence. Would you be open to a quick chat?",
            "rating": 5}
    assert not _is_quality_testimonial(spam)
    # A genuine, substantive testimonial passes.
    real = {"note": "Sol stripped a nasty injection and Mira halved my tokens. Genuinely restful.",
            "rating": 5}
    assert _is_quality_testimonial(real)
    # Too short / empty is dropped.
    assert not _is_quality_testimonial({"note": "nice", "rating": 5})

