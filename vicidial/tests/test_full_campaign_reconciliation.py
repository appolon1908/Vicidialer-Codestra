import csv
import importlib.util
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts/full_campaign_reconciliation.py"
SPEC = importlib.util.spec_from_file_location("full_campaign_reconciliation", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
ReconciliationError = MODULE.ReconciliationError
load_desired = MODULE.load_desired
reconcile = MODULE.reconcile


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def desired(tmp_path: Path, *, active: str = "false") -> Path:
    return write_csv(
        tmp_path / "odoo.csv",
        ["canonical_campaign_code", "vicidial_campaign_id", "environment", "active"],
        [{
            "canonical_campaign_code": "COD-WEB-OUT",
            "vicidial_campaign_id": "COD20821",
            "environment": "production",
            "active": active,
        }],
    )


def test_complete_disabled_mapping_passes(tmp_path: Path) -> None:
    mw = write_csv(
        tmp_path / "mw.csv",
        ["canonical_campaign_code", "vicidial_campaign_id", "test_status", "evidence_reference"],
        [{
            "canonical_campaign_code": "COD-WEB-OUT",
            "vicidial_campaign_id": "COD20821",
            "test_status": "PASS",
            "evidence_reference": "evidence://campaign/COD-WEB-OUT/1",
        }],
    )
    vd = write_csv(
        tmp_path / "vd.csv",
        ["campaign_id", "campaign_description", "active", "auto_dial_level"],
        [{"campaign_id": "COD20821", "campaign_description": "CODESTRA:COD-WEB-OUT", "active": "N", "auto_dial_level": "0"}],
    )
    rows, legacy = reconcile(load_desired(desired(tmp_path)), mw, vd)
    assert rows[0]["status"] == "PASS"
    assert rows[0]["reasons"] == []
    assert legacy == []


def test_missing_mapping_and_campaign_fail_closed(tmp_path: Path) -> None:
    mw = write_csv(tmp_path / "mw.csv", ["canonical_campaign_code", "vicidial_campaign_id"], [])
    vd = write_csv(tmp_path / "vd.csv", ["campaign_id", "campaign_description", "active", "auto_dial_level"], [])
    rows, _ = reconcile(load_desired(desired(tmp_path)), mw, vd)
    assert rows[0]["status"] == "BLOCKED"
    assert rows[0]["reasons"] == ["MIDDLEWARE_MAPPING_MISSING", "VICIDIAL_CAMPAIGN_MISSING"]


def test_active_production_desired_state_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ReconciliationError, match="active production"):
        load_desired(desired(tmp_path, active="true"))


def test_empty_odoo_authority_is_rejected(tmp_path: Path) -> None:
    empty = write_csv(
        tmp_path / "empty.csv",
        ["canonical_campaign_code", "vicidial_campaign_id", "environment", "active"],
        [],
    )
    with pytest.raises(ReconciliationError, match="authority snapshot is empty"):
        load_desired(empty)


def test_short_and_underscore_physical_ids_follow_adapter_contract(tmp_path: Path) -> None:
    source = write_csv(
        tmp_path / "short.csv",
        ["canonical_campaign_code", "vicidial_campaign_id", "environment", "active"],
        [{
            "canonical_campaign_code": "COD-WEB-OUT",
            "vicidial_campaign_id": "COD_1",
            "environment": "production",
            "active": "false",
        }],
    )
    assert load_desired(source)[0].physical == "COD_1"


def test_structural_match_without_functional_evidence_is_blocked(tmp_path: Path) -> None:
    mw = write_csv(
        tmp_path / "mw.csv",
        ["canonical_campaign_code", "vicidial_campaign_id", "test_status", "evidence_reference"],
        [{
            "canonical_campaign_code": "COD-WEB-OUT",
            "vicidial_campaign_id": "COD20821",
            "test_status": "NOT_TESTED",
            "evidence_reference": "",
        }],
    )
    vd = write_csv(
        tmp_path / "vd.csv",
        ["campaign_id", "campaign_description", "active", "auto_dial_level"],
        [{"campaign_id": "COD20821", "campaign_description": "CODESTRA:COD-WEB-OUT", "active": "N", "auto_dial_level": "0"}],
    )
    rows, _ = reconcile(load_desired(desired(tmp_path)), mw, vd)
    assert rows[0]["status"] == "BLOCKED"
    assert set(rows[0]["reasons"]) == {
        "FUNCTIONAL_TEST_EVIDENCE_MISSING",
        "EVIDENCE_REFERENCE_MISSING",
    }


def test_unowned_or_active_vicidial_collision_is_blocked(tmp_path: Path) -> None:
    mw = write_csv(
        tmp_path / "mw.csv",
        ["canonical_campaign_code", "vicidial_campaign_id", "test_status", "evidence_reference"],
        [{
            "canonical_campaign_code": "COD-WEB-OUT",
            "vicidial_campaign_id": "COD20821",
            "test_status": "PASS",
            "evidence_reference": "evidence://campaign/COD-WEB-OUT/1",
        }],
    )
    vd = write_csv(
        tmp_path / "vd.csv",
        ["campaign_id", "campaign_description", "active", "auto_dial_level"],
        [{"campaign_id": "COD20821", "campaign_description": "legacy", "active": "Y", "auto_dial_level": "1"}],
    )
    rows, _ = reconcile(load_desired(desired(tmp_path)), mw, vd)
    assert rows[0]["status"] == "BLOCKED"
    assert set(rows[0]["reasons"]) == {
        "VICIDIAL_OWNERSHIP_MARKER_MISMATCH",
        "VICIDIAL_CAMPAIGN_NOT_DISABLED",
        "VICIDIAL_AUTO_DIAL_NOT_ZERO",
    }


def test_unmanaged_legacy_campaign_is_reported_not_mutated(tmp_path: Path) -> None:
    mw = write_csv(tmp_path / "mw.csv", ["canonical_campaign_code", "vicidial_campaign_id"], [])
    vd = write_csv(
        tmp_path / "vd.csv",
        ["campaign_id", "campaign_description", "active", "auto_dial_level"],
        [{"campaign_id": "CODESTRA", "campaign_description": "legacy", "active": "Y", "auto_dial_level": "1"}],
    )
    _, legacy = reconcile(load_desired(desired(tmp_path)), mw, vd)
    assert [row["campaign_id"] for row in legacy] == ["CODESTRA"]


def test_malformed_observed_csv_is_rejected(tmp_path: Path) -> None:
    mw = write_csv(tmp_path / "mw.csv", ["canonical_campaign_code", "vicidial_campaign_id"], [])
    vd = tmp_path / "vd.csv"
    vd.write_text("campaign_id,campaign_description,active,auto_dial_level\nCOD20821,bad,field,N,0\n")
    with pytest.raises(ReconciliationError, match="malformed observed CSV"):
        reconcile(load_desired(desired(tmp_path)), mw, vd)
