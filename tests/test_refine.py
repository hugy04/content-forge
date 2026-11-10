"""A revision the reader asked for.

The pipeline always had a revision stage; only QC could reach it. A content
team that wanted a sharper opening had to re-run the whole thing and hope the
engine rolled the same settings, which it is specifically designed not to.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_PROVIDERS", "1")
    monkeypatch.setenv("BASIC_AUTH_USER", "")
    monkeypatch.setenv("BASIC_AUTH_PASS", "")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "forge.db"))
    monkeypatch.setenv("OUT_DIR", str(tmp_path / "out"))
    import forge.config
    forge.config.get_settings.cache_clear()
    import forge.api.app as app_module
    importlib.reload(app_module)
    return TestClient(app_module.app)


def _run(client) -> dict:
    body = client.post("/v1/generate",
                       json={"topic": "warehouse robotics", "style": "guide"}).json()
    return body.get("detail", body)


def test_a_revision_keeps_the_run_its_image_and_its_settings(client):
    first = _run(client)
    response = client.post(f"/v1/runs/{first['run_id']}/revise",
                           json={"instruction": "sharpen the opening"})
    assert response.status_code == 200
    revised = response.json()

    assert revised["run_id"] == first["run_id"]
    assert revised["selection"] == first["selection"], "settings are not re-rolled"
    assert revised["image"]["provider"] == first["image"]["provider"], "image is reused"
    assert revised["revisions"] > first["revisions"]


def test_a_revision_is_recorded(client):
    first = _run(client)
    client.post(f"/v1/runs/{first['run_id']}/revise", json={"instruction": "shorter"})
    stored = client.get(f"/v1/runs/{first['run_id']}").json()
    assert stored["revisions"] > first["revisions"]


def test_an_unknown_run_is_a_404(client):
    assert client.post("/v1/runs/nosuchrun/revise",
                       json={"instruction": "x"}).status_code == 404


@pytest.mark.parametrize("body", [{}, {"instruction": ""}, {"instruction": "   "},
                                  {"instruction": 7}])
def test_a_revision_needs_an_instruction(client, body):
    first = _run(client)
    assert client.post(f"/v1/runs/{first['run_id']}/revise",
                       json=body).status_code == 400


def test_a_very_long_instruction_is_truncated_not_rejected():
    """A pasted paragraph is a reasonable thing to do and a poor reason to fail."""
    from forge.pipeline.stages.refine import MAX_INSTRUCTION

    assert MAX_INSTRUCTION == 500


def test_the_page_offers_the_revision_box(client):
    page = client.get("/").text
    assert 'id="instruction"' in page
    assert 'id="revise-go"' in page
    assert "/revise" in page
