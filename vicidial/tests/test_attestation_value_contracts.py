from __future__ import annotations

import runpy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "vicidial" / "scripts" / "verify_oci_export.py"
WORKFLOW = ROOT / ".github" / "workflows" / "vicidial-adapter-container.yml"
SOURCE_REPOSITORY = "https://github.com/appolon1908-hue/Vicidialer-Codestra"
SOURCE_SHA = "a" * 40


def _validator(name: str):
    return runpy.run_path(str(SCRIPT))[name]


def _spdx_predicate() -> dict:
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "test-sbom",
        "documentNamespace": "https://codestra.example/spdx/test",
        "creationInfo": {
            "creators": ["Tool: codestra-test"],
            "created": "2026-01-01T00:00:00Z",
        },
        "packages": [
            {
                "name": "codestra-vicidial-restricted-adapter",
                "SPDXID": "SPDXRef-Package-adapter",
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "copyrightText": "NOASSERTION",
                "checksums": [
                    {"algorithm": "SHA256", "checksumValue": "b" * 64}
                ],
            }
        ],
        "relationships": [
            {
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relationshipType": "DESCRIBES",
                "relatedSpdxElement": "SPDXRef-Package-adapter",
            }
        ],
    }


def _slsa_predicate(
    *,
    source_uri: str = SOURCE_REPOSITORY + ".git",
    source_digest_key: str = "sha1",
    source_digest: str = SOURCE_SHA,
    vcs_source: str | None = SOURCE_REPOSITORY,
    vcs_revision: str = SOURCE_SHA,
) -> dict:
    vcs = {"revision": vcs_revision}
    if vcs_source is not None:
        vcs["source"] = vcs_source
    return {
        "buildDefinition": {
            "buildType": "https://github.com/moby/buildkit/slsa/v1",
            "resolvedDependencies": [
                {
                    "uri": source_uri,
                    "digest": {source_digest_key: source_digest},
                },
                {
                    "uri": "pkg:docker/python@3.11.16-alpine",
                    "digest": {"sha256": "c" * 64},
                },
            ],
            "externalParameters": {},
            "internalParameters": {},
        },
        "runDetails": {
            "builder": {
                "id": "https://github.com/codestra/test-builder",
                "builderDependencies": [
                    {
                        "uri": "pkg:docker/moby/buildkit@test",
                        "digest": {"sha256": "d" * 64},
                    }
                ],
            },
            "metadata": {
                "invocationId": "test-invocation",
                "startedOn": "2026-01-01T00:00:00Z",
                "finishedOn": "2026-01-01T00:00:01Z",
                "buildkit_metadata": {"vcs": vcs},
            },
            "byproducts": [
                {
                    "name": "build-log",
                    "mediaType": "text/plain",
                    "digest": {"sha256": "e" * 64},
                }
            ],
        },
    }


def test_spdx_accepts_complete_value_contract():
    _validator("validate_spdx_predicate")(_spdx_predicate())


def test_spdx_rejects_invalid_package_identifier():
    predicate = _spdx_predicate()
    predicate["packages"][0]["SPDXID"] = "anything"
    with pytest.raises(ValueError, match="valid SPDX element identifier"):
        _validator("validate_spdx_predicate")(predicate)


@pytest.mark.parametrize(
    "algorithm,value,match",
    [
        ("BANANA", "a" * 64, "algorithm is not supported"),
        ("SHA256", "g" * 64, "lowercase hexadecimal"),
        ("SHA256", "a", "length is invalid"),
    ],
)
def test_spdx_rejects_invalid_checksum_contract(algorithm, value, match):
    predicate = _spdx_predicate()
    predicate["packages"][0]["checksums"] = [
        {"algorithm": algorithm, "checksumValue": value}
    ]
    with pytest.raises(ValueError, match=match):
        _validator("validate_spdx_predicate")(predicate)


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("spdxElementId", "anything", "valid SPDX element identifier"),
        ("relatedSpdxElement", "DocumentRef-bad", "valid SPDX element identifier"),
        ("relationshipType", "MADE_UP", "not supported by SPDX 2.3"),
    ],
)
def test_spdx_rejects_invalid_relationship_contract(field, value, match):
    predicate = _spdx_predicate()
    predicate["relationships"][0][field] = value
    with pytest.raises(ValueError, match=match):
        _validator("validate_spdx_predicate")(predicate)


@pytest.mark.parametrize(
    "digest,match",
    [
        ({"sha256": "not-a-digest"}, "lowercase hexadecimal"),
        ({"sha256": "a" * 62}, "length is invalid"),
        ({"gitCommit": "a" * 39}, "length is invalid"),
        ({"sha 256": "a" * 64}, "algorithm is invalid"),
    ],
)
def test_slsa_rejects_invalid_digest_encoding(digest, match):
    with pytest.raises(ValueError, match=match):
        _validator("validate_digest_set")(digest, "test descriptor")


@pytest.mark.parametrize(
    "source_uri,digest_key",
    [
        (SOURCE_REPOSITORY + ".git", "sha1"),
        (
            "git+" + SOURCE_REPOSITORY + "@refs/pull/2/head",
            "gitCommit",
        ),
    ],
)
def test_slsa_accepts_exact_source_material(source_uri, digest_key):
    _validator("validate_slsa_provenance")(
        _slsa_predicate(
            source_uri=source_uri,
            source_digest_key=digest_key,
        ),
        SOURCE_SHA,
    )


def test_slsa_rejects_missing_source_material():
    predicate = _slsa_predicate()
    predicate["buildDefinition"]["resolvedDependencies"] = predicate[
        "buildDefinition"
    ]["resolvedDependencies"][1:]
    with pytest.raises(ValueError, match="exactly one resolved source dependency"):
        _validator("validate_slsa_provenance")(predicate, SOURCE_SHA)


def test_slsa_rejects_wrong_source_repository():
    predicate = _slsa_predicate(source_uri="https://github.com/example/other.git")
    with pytest.raises(ValueError, match="exactly one resolved source dependency"):
        _validator("validate_slsa_provenance")(predicate, SOURCE_SHA)


def test_slsa_rejects_contradictory_source_commit():
    predicate = _slsa_predicate(source_digest="f" * 40)
    with pytest.raises(ValueError, match="source dependency differs from exact head"):
        _validator("validate_slsa_provenance")(predicate, SOURCE_SHA)


def test_slsa_rejects_wrong_metadata_source():
    predicate = _slsa_predicate(
        vcs_source="https://github.com/example/other"
    )
    with pytest.raises(ValueError, match="source repository is invalid"):
        _validator("validate_slsa_provenance")(predicate, SOURCE_SHA)


def test_attested_workflow_uses_exact_git_source_context():
    text = WORKFLOW.read_text(encoding="utf-8")
    runnable, attested_and_after = text.split(
        "- name: Export attested linux/amd64 OCI candidate", 1
    )
    attested = attested_and_after.split("- name: Test container contract", 1)[0]
    exact_context = (
        'context: "https://github.com/${{ github.repository }}.git#'
        '${{ env.SOURCE_SHA }}"'
    )
    assert exact_context in runnable
    assert exact_context in attested
    assert "context: ." not in runnable
    assert "github-token: ${{ github.token }}" in runnable
    assert "github-token: ${{ github.token }}" in attested
    assert "SOURCE_DATE_EPOCH=1767225600" in runnable
    assert "SOURCE_DATE_EPOCH=1767225600" in attested
    assert "provenance: mode=max" in attested


def test_spdx_accepts_external_document_reference_with_plus():
    predicate = _spdx_predicate()
    predicate["relationships"][0]["relatedSpdxElement"] = (
        "DocumentRef-upstream+release:SPDXRef-Package.external"
    )
    _validator("validate_spdx_predicate")(predicate)


def test_slsa_rejects_invalid_base64_content():
    descriptor = {"content": "not base64!"}
    with pytest.raises(ValueError, match="not valid base64"):
        _validator("validate_resource_descriptor")(descriptor, "descriptor")


def test_slsa_accepts_exact_commit_suffix_in_source_uri():
    _validator("validate_slsa_provenance")(
        _slsa_predicate(source_uri=SOURCE_REPOSITORY + ".git@" + SOURCE_SHA),
        SOURCE_SHA,
    )


def test_slsa_rejects_invalid_exact_source_sha():
    with pytest.raises(ValueError, match="exact source SHA is invalid"):
        _validator("validate_slsa_provenance")(
            _slsa_predicate(source_digest="a" * 38),
            "a" * 38,
        )
