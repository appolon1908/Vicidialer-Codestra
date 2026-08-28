from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "vicidial" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from slsa_attestation_contracts import validate_slsa_provenance  # noqa: E402

SOURCE_REPOSITORY = "https://github.com/appolon1908-hue/Vicidialer-Codestra"
SOURCE_SHA = "a" * 40


def _source_descriptor(*, digest: str = SOURCE_SHA) -> dict:
    return {
        "uri": f"{SOURCE_REPOSITORY}.git#{SOURCE_SHA}",
        "digest": {"sha1": digest},
        "path": "vicidial/docker/Dockerfile",
    }


def _buildkit_git_predicate(*, include_vcs: bool = False) -> dict:
    buildkit_metadata = {"source": {"locations": {}}}
    if include_vcs:
        buildkit_metadata["vcs"] = {
            "revision": SOURCE_SHA,
            "source": SOURCE_REPOSITORY,
        }
    return {
        "buildDefinition": {
            "buildType": (
                "https://github.com/moby/buildkit/blob/master/docs/"
                "attestations/slsa-definitions.md"
            ),
            "resolvedDependencies": [
                _source_descriptor(),
                {
                    "uri": "pkg:docker/python@3.11.16-alpine",
                    "digest": {"sha256": "b" * 64},
                },
            ],
            "externalParameters": {
                "configSource": _source_descriptor(),
                "request": {
                    "root": {"configSource": _source_descriptor()},
                },
            },
            "internalParameters": {},
        },
        "runDetails": {
            "builder": {"id": "https://github.com/codestra/test-builder"},
            "metadata": {
                "invocationId": "test-invocation",
                "startedOn": "2026-01-01T00:00:00Z",
                "finishedOn": "2026-01-01T00:00:01Z",
                "buildkit_metadata": buildkit_metadata,
            },
        },
    }


def test_accepts_buildkit_git_context_without_vcs_metadata():
    validate_slsa_provenance(_buildkit_git_predicate(), SOURCE_SHA)


def test_accepts_matching_optional_vcs_metadata():
    validate_slsa_provenance(_buildkit_git_predicate(include_vcs=True), SOURCE_SHA)


def test_rejects_mismatched_resolved_source_dependency():
    predicate = _buildkit_git_predicate()
    predicate["buildDefinition"]["resolvedDependencies"][0]["digest"][
        "sha1"
    ] = "f" * 40
    with pytest.raises(ValueError, match="source dependency differs"):
        validate_slsa_provenance(predicate, SOURCE_SHA)


def test_rejects_mismatched_external_config_source():
    predicate = _buildkit_git_predicate()
    predicate["buildDefinition"]["externalParameters"]["configSource"][
        "digest"
    ]["sha1"] = "f" * 40
    with pytest.raises(ValueError, match="external config source differs"):
        validate_slsa_provenance(predicate, SOURCE_SHA)


def test_rejects_mismatched_root_config_source():
    predicate = _buildkit_git_predicate()
    predicate["buildDefinition"]["externalParameters"]["request"]["root"][
        "configSource"
    ]["digest"]["sha1"] = "f" * 40
    with pytest.raises(ValueError, match="root config source differs"):
        validate_slsa_provenance(predicate, SOURCE_SHA)


def test_rejects_conflicting_optional_vcs_metadata():
    predicate = _buildkit_git_predicate(include_vcs=True)
    predicate["runDetails"]["metadata"]["buildkit_metadata"]["vcs"][
        "revision"
    ] = "f" * 40
    with pytest.raises(ValueError, match="source revision differs"):
        validate_slsa_provenance(predicate, SOURCE_SHA)


def test_rejects_conflicting_hex_revision_in_source_uri():
    predicate = _buildkit_git_predicate()
    predicate["buildDefinition"]["resolvedDependencies"][0]["uri"] = (
        f"{SOURCE_REPOSITORY}.git#{'f' * 40}"
    )
    with pytest.raises(ValueError, match="URI revision differs"):
        validate_slsa_provenance(predicate, SOURCE_SHA)
