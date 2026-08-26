from __future__ import annotations

import importlib.metadata
import importlib.util

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_starlette_testclient_uses_httpx2_without_classic_httpx():
    assert importlib.metadata.version("starlette") == "1.6.0"
    assert importlib.metadata.version("httpx2") == "2.12.0"
    assert importlib.util.find_spec("httpx") is None

    app = FastAPI()

    @app.get("/proof")
    def proof():
        return {"transport": "httpx2"}

    with TestClient(app) as client:
        response = client.get("/proof")
    assert response.status_code == 200
    assert response.json() == {"transport": "httpx2"}
