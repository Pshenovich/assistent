"""/docs наружу отдаёт только веб-страницы и ассеты, внутренние файлы — 404."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import usage_server


@pytest.fixture(scope="module")
def client():
    return TestClient(usage_server.app, base_url="https://testserver")


@pytest.mark.parametrize(
    "path",
    [
        "/docs/migration-runbook.md",
        "/docs/beget-merge-plan.md",
        "/docs/zoom-marketplace-credentials.template.txt",
        "/docs/google-demo-subtitles.srt",
        "/docs/agents/dashboards.md",
        "/docs/fonts/UNBOUNDED-LICENSE.txt",
        "/docs/MIGRATION-RUNBOOK.MD",
        "/docs/",
    ],
)
def test_internal_docs_hidden(client, path):
    assert client.get(path).status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/docs/support.html",
        "/docs/zoom.html",
        "/docs/zoom-test-plan.html",
        "/docs/landing.js",
        "/docs/fonts/unbounded-latin-wght.woff2",
        "/docs/zoom-review/architecture.png",
        "/docs/google-verification/orbita-logo-120.png",
    ],
)
def test_public_docs_served(client, path):
    assert client.get(path).status_code == 200
