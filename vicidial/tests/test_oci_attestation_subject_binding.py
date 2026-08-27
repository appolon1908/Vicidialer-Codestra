from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/vicidial-adapter-container.yml"


def test_attested_oci_export_binds_buildkit_statements_to_exact_image_name():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    expected = (
        "outputs: type=oci,dest=evidence/vicidial-adapter.oci.tar,"
        "name=codestra/vicidial-restricted-adapter:${{ env.SOURCE_SHA }}"
    )
    assert expected in workflow
    assert "sbom: true" in workflow
    assert "provenance: mode=max" in workflow
    assert "python vicidial/scripts/verify_oci_export.py" in workflow
