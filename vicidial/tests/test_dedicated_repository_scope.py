from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_dedicated_repository_excludes_unrelated_monorepo_artifacts():
    forbidden = (
        "tools/odoo_vertical_staging_acceptance.py",
        "tests/test_odoo_n8n_production_preflight.py",
        "operations/odoo-n8n",
    )
    present = [path for path in forbidden if (ROOT / path).exists()]
    assert present == []


def test_dedicated_repository_keeps_vicidial_integration_authority():
    required = (
        "campaign_provisioning",
        "deploy/vicidial-restricted-adapter",
        "vicidial/src/codestra_vicidial",
        "vicidial/docker",
        "vicidial/ops",
        "scripts/vicidial_sync.py.candidate",
        "scripts/canonical_event_client.py.reference",
        "tests/test_production_vicidial_sync.py",
        "tests/test_synthetic_campaign_lifecycle.py",
        ".github/workflows/vicidial-adapter-source.yml",
        ".github/workflows/vicidial-adapter-container.yml",
    )
    missing = [path for path in required if not (ROOT / path).exists()]
    assert missing == []
