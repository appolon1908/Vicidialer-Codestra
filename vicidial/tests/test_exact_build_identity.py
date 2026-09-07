from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/vicidial-adapter-container.yml"
EXACT_GIT_CONTEXT = (
    'context: "https://github.com/${{ github.repository }}.git#'
    '${{ env.SOURCE_SHA }}"'
)


def test_runnable_and_attested_builds_share_exact_source_and_timestamp() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.count("docker/build-push-action@v6") == 2
    assert text.count(EXACT_GIT_CONTEXT) == 2
    assert "\n          context: .\n" not in text
    assert text.count("SOURCE_DATE_EPOCH=1767225600") == 2
    assert text.count("BUILD_DATE=2026-01-01T00:00:00Z") == 2
    assert text.count("GIT_AUTH_TOKEN.github.com=${{ github.token }}") == 2
    assert "Verify exported OCI is the tested exact image" in text
