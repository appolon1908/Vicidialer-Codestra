#!/usr/bin/env python3
"""Export the reviewed, machine-readable restricted adapter binding contract."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SOURCE_ROOT))

from codestra_vicidial.app import (
    _AUTHENTICATION_HEADERS,
    _COMMAND_MODEL_BY_ROUTE,
    _PROTECTED_SCOPE_BY_ROUTE,
)


_MUTATING_ROUTES = frozenset({
    "/v1/campaigns/provision-disabled",
    "/v1/campaigns/disable",
    "/v1/agents/sync",
    "/v1/agents/provision-disabled",
    "/v1/agents/disable",
    "/v1/calls/originate",
    "/v1/calls/internal/originate",
    "/v1/calls/internal/{operation_id}/hangup",
    "/v1/leads/publish",
    "/v1/callbacks/create",
    "/v1/reconciliation/campaigns/provision-disabled",
    "/v1/transfer-policies/sync",
})

_OPERATION_BY_ROUTE = {
    "/v1/idempotency/lookup": "idempotency.lookup",
    "/v1/campaigns/validate": "campaign.validate",
    "/v1/campaigns/provision-disabled": "campaign.provision-disabled",
    "/v1/campaigns/disable": "campaign.disable",
    "/v1/agents/sync": "agent.sync",
    "/v1/agents/provision-disabled": "agent.provision-disabled",
    "/v1/agents/disable": "agent.disable",
    "/v1/calls/originate": "call.external.originate",
    "/v1/calls/internal/originate": "call.internal.originate",
    "/v1/calls/internal/{operation_id}/hangup": "call.internal.hangup",
    "/v1/leads/publish": "lead.publish",
    "/v1/callbacks/create": "callback.create",
    "/v1/reconciliation/campaigns/provision-disabled": "reconciliation.campaign.provision-disabled",
    "/v1/transfer-policies/sync": "transfer-policy.sync",
    "/v1/reconciliation/run": "reconciliation.run",
}


def build_contract() -> dict[str, Any]:
    routes = []
    for method, path in sorted(_PROTECTED_SCOPE_BY_ROUTE):
        method_upper = method.upper()
        if path in _MUTATING_ROUTES and method_upper != "POST":
            raise RuntimeError(f"mutating route must be POST: {method} {path}")
        if method_upper == "POST" and path not in _OPERATION_BY_ROUTE:
            raise RuntimeError(f"POST route missing operation mapping: {path}")
        request_model = (
            _COMMAND_MODEL_BY_ROUTE[path].__name__
            if method_upper == "POST" and path in _COMMAND_MODEL_BY_ROUTE
            else None
        )
        routes.append({
            "method": method_upper,
            "path": path,
            "operation": _OPERATION_BY_ROUTE.get(path),
            "required_scope": _PROTECTED_SCOPE_BY_ROUTE[(method, path)],
            "request_model": request_model,
            "mutation": path in _MUTATING_ROUTES,
            "idempotency": (
                "body_key" if path == "/v1/idempotency/lookup"
                else "header_required" if method_upper == "POST"
                else "not_applicable"
            ),
        })
    return {
        "contract_version": 1,
        "api_prefix": "/v1",
        "transport": {
            "protocol": "https",
            "tls": "mTLS through the separately approved private route",
            "signature": "HMAC-SHA256 v2",
            "service_identity": "codestra-middleware",
            "required_headers": list(_AUTHENTICATION_HEADERS),
        },
        "safety": {
            "default_mutations": "disabled",
            "forbidden_caller_inputs": [
                "arbitrary_sql", "shell", "dialplan", "trunk", "url", "credential",
            ],
            "uncertain_mutation": "reconciliation_required",
        },
        "routes": routes,
    }


def write_contract(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_contract(), indent=2, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path,
        default=Path("vicidial/config/restricted-adapter-contract.json"),
    )
    args = parser.parse_args()
    write_contract(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
