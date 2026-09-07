from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/vicidial-adapter-source.yml"


def test_source_workflow_checks_out_the_exact_pull_request_head():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "SOURCE_SHA: ${{ github.event.pull_request.head.sha }}" in text
    assert "BASE_SHA: ${{ github.event.pull_request.base.sha }}" in text
    assert "ref: ${{ env.SOURCE_SHA }}" in text
    assert "persist-credentials: false" in text
    assert 'test "$(git rev-parse HEAD)" = "${SOURCE_SHA}"' in text
    assert "git status --porcelain --untracked-files=all" in text
    assert '${BASE_SHA}..${SOURCE_SHA}' in text


def test_source_workflow_remains_fail_closed_and_read_only():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "permissions:\n  contents: read" in text
    assert "Scan candidate tree" in text
    assert "Scan PR history" in text
    assert "Scan candidate diff" in text
    assert "Reject forbidden artifacts" in text
    assert "Validate systemd units statically" in text
    assert "upload-artifact" not in text
