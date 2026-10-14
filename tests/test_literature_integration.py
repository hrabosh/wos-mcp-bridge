"""Opt-in real OA article test; normal test runs stay entirely offline."""

import os

import pytest

from literature_client import LiteratureClient


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("RUN_LITERATURE_INTEGRATION") != "1",
    reason="Set RUN_LITERATURE_INTEGRATION=1 for live API requests",
)
async def test_available_open_access_article():
    # PeerJ's open-access work used in the official OpenAlex API documentation.
    client = LiteratureClient(openalex_api_key=os.environ.get("OPENALEX_API_KEY", ""))
    try:
        resolved = await client.resolve("10.7717/peerj.4375")
        assert resolved["doi"] == "10.7717/peerj.4375"
        assert resolved["pmcid"]
        assert resolved["access"]["status"] == "open_access"
        assert "OpenAlex" in resolved["source"], resolved["warnings"]
        assert any(location["source"] == "OpenAlex" for location in resolved["oa_locations"])
        article = await client.get_article(resolved["pmcid"], max_chars=1000)
        assert article["access"]["content_status"] == "full_text"
        assert article["license"]
        assert article["sections"]
        assert article["text_budget"]["returned_chars"] <= 1000
        section = next(
            s for s in article["sections"] if s["total_chars"] > 0 and s["kind"] == "sec"
        )
        fetched = await client.get_section(resolved["doi"], section["id"], max_chars=500)
        assert fetched["section"]["text"]
        assert len(fetched["section"]["text"]) <= 500
        assert fetched["section"]["heading"] == section["heading"]
        assert all(r["cached"] for r in fetched["retrieval"])
    finally:
        await client.aclose()
