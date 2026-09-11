from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from codestra_vicidial.app import _COMMAND_MODEL_BY_ROUTE, _PROTECTED_SCOPE_BY_ROUTE


ROOT = Path(__file__).parents[2]
_SCRIPT = ROOT / "vicidial/scripts/export_adapter_contract.py"
_SPEC = importlib.util.spec_from_file_location("export_adapter_contract", _SCRIPT)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("adapter contract generator is not importable")
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
build_contract = _MODULE.build_contract


MANIFEST = ROOT / "vicidial/config/restricted-adapter-contract.json"


def test_manifest_is_generated_from_the_live_route_maps() -> None:
    document = json.loads(MANIFEST.read_text())
    assert document == build_contract()
    assert document["transport"]["signature"] == "HMAC-SHA256 v2"
    assert document["transport"]["required_headers"] == [
        "X-Service-Identity",
        "X-Service-Scopes",
        "X-Request-Timestamp",
        "X-Request-Nonce",
        "X-Request-Signature",
        "X-Signature-Version",
    ]
    assert document["safety"]["default_mutations"] == "disabled"


def test_manifest_has_one_entry_for_every_protected_route() -> None:
    document = json.loads(MANIFEST.read_text())
    expected = {(method.upper(), path): scope for (method, path), scope in _PROTECTED_SCOPE_BY_ROUTE.items()}
    actual = {(item["method"], item["path"]): item for item in document["routes"]}
    assert set(actual) == set(expected)
    assert all(actual[key]["required_scope"] == scope for key, scope in expected.items())
    assert len(actual) == len(document["routes"])


def test_every_command_route_publishes_its_request_model() -> None:
    document = json.loads(MANIFEST.read_text())
    routes = {(item["method"], item["path"]): item for item in document["routes"]}
    for path, model in _COMMAND_MODEL_BY_ROUTE.items():
        item = routes[("POST", path)]
        assert item["request_model"] == model.__name__
        assert item["idempotency"] == "header_required" or path == "/v1/idempotency/lookup"
    assert routes[("POST", "/v1/idempotency/lookup")]["idempotency"] == "body_key"


def test_manifest_never_describes_a_caller_controlled_execution_primitive() -> None:
    encoded = MANIFEST.read_text().lower()
    for forbidden in ("select ", "insert ", "update ", "delete ", "exec(", "ami", "password"):
        assert forbidden not in encoded
