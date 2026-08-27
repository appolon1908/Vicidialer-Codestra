from __future__ import annotations

import gzip
import hashlib
import io
import json
import runpy
import sys
import tarfile

import pytest


SCRIPT = "vicidial/scripts/verify_oci_export.py"
OCI_INDEX = "application/vnd.oci.image.index.v1+json"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_CONFIG = "application/vnd.oci.image.config.v1+json"
OCI_EMPTY = "application/vnd.oci.empty.v1+json"
IN_TOTO = "application/vnd.in-toto+json"
SBOM = "https://spdx.dev/Document"
PROVENANCE = "https://slsa.dev/provenance/v1"


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _write_member(archive: tarfile.TarFile, name: str, payload: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    archive.addfile(info, io.BytesIO(payload))


def _descriptor(payload: bytes, media_type: str | None = None, **extra) -> dict:
    value = {
        "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
        **extra,
    }
    if media_type:
        value["mediaType"] = media_type
    return value


def _blob_name(descriptor: dict) -> str:
    return "blobs/sha256/" + descriptor["digest"].split(":", 1)[1]


def _write_archive(path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w") as archive:
        for name, payload in members.items():
            _write_member(archive, name, payload)


def _read_members(path) -> dict[str, bytes]:
    with tarfile.open(path, "r") as archive:
        return {
            item.name: archive.extractfile(item).read()
            for item in archive
            if item.isfile()
        }


def _spdx_predicate() -> dict:
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "sbom",
        "documentNamespace": "https://codestra.example/spdx/test",
        "creationInfo": {
            "creators": ["Tool: buildkit-test-fixture"],
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
                    {
                        "algorithm": "SHA256",
                        "checksumValue": "a" * 64,
                    }
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


def _slsa_predicate(source_sha: str) -> dict:
    return {
        "buildDefinition": {
            "buildType": (
                "https://github.com/moby/buildkit/blob/master/docs/"
                "attestations/slsa-definitions.md"
            ),
            "resolvedDependencies": [
                {
                    "uri": "git+https://github.com/appolon1908-hue/"
                    "Vicidialer-Codestra@refs/pull/2/head",
                    "digest": {"gitCommit": source_sha},
                }
            ],
            "externalParameters": {},
            "internalParameters": {},
        },
        "runDetails": {
            "builder": {
                "id": "https://github.com/codestra/test-builder",
                "builderDependencies": [
                    {"uri": "pkg:docker/moby/buildkit@test"}
                ],
                "version": {"buildkit": "test"},
            },
            "metadata": {
                "invocationId": "test-invocation",
                "startedOn": "2026-01-01T00:00:00Z",
                "finishedOn": "2026-01-01T00:00:01Z",
                "buildkit_metadata": {"vcs": {"revision": source_sha}},
            },
            "byproducts": [
                {
                    "name": "build-log",
                    "digest": {"sha256": "b" * 64},
                    "mediaType": "text/plain",
                }
            ],
        },
    }


def _statement(
    predicate_type: str,
    image_digest: str,
    source_sha: str,
    *,
    subject_digest=None,
    mutation: str | None = None,
) -> bytes:
    digest = subject_digest or image_digest.split(":", 1)[1]
    if predicate_type == SBOM:
        predicate = _spdx_predicate()
        if mutation == "spdx_version":
            predicate["spdxVersion"] = "SPDX-2.2"
    elif predicate_type == PROVENANCE:
        predicate = _slsa_predicate(source_sha)
        if mutation == "slsa_schema":
            predicate.pop("runDetails")
        elif mutation == "slsa_revision":
            predicate["runDetails"]["metadata"]["buildkit_metadata"]["vcs"][
                "revision"
            ] = "f" * 40
        elif mutation == "slsa_resolved_dependency":
            predicate["buildDefinition"]["resolvedDependencies"] = [None]
        elif mutation == "slsa_builder_dependency":
            predicate["runDetails"]["builder"]["builderDependencies"] = [None]
        elif mutation == "slsa_byproduct":
            predicate["runDetails"]["byproducts"] = [None]
    else:
        predicate = {"unsupported": True}
    if mutation == "empty_predicate":
        predicate = {}
    return _json_bytes({
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [{
            "name": "pkg:docker/codestra/vicidial-restricted-adapter@test",
            "digest": {"sha256": digest},
        }],
        "predicateType": predicate_type,
        "predicate": predicate,
    })


def _build_archive(
    path,
    source_sha: str,
    architecture: str = "amd64",
    *,
    direct_root: bool = False,
    include_attestations: bool = True,
    include_sbom: bool = True,
    include_provenance: bool = True,
    image_schema: object = 2,
    image_mutation: str | None = None,
    attestation_mutation: str | None = None,
    tamper_target: str | None = None,
) -> tuple[str, list[str]]:
    members: dict[str, bytes] = {
        "oci-layout": _json_bytes({"imageLayoutVersion": "1.0.0"}),
    }

    payload_one = b"deterministic layer one\n"
    raw_one = gzip.compress(payload_one, mtime=0)
    descriptor_one = _descriptor(
        raw_one, "application/vnd.oci.image.layer.v1.tar+gzip"
    )
    diff_one = "sha256:" + hashlib.sha256(payload_one).hexdigest()
    members[_blob_name(descriptor_one)] = raw_one

    payload_two = b"deterministic layer two\n"
    raw_two = gzip.compress(payload_two, mtime=0)
    descriptor_two = _descriptor(
        raw_two, "application/vnd.oci.image.layer.v1.tar+gzip"
    )
    diff_two = "sha256:" + hashlib.sha256(payload_two).hexdigest()

    layers = [descriptor_one]
    diff_ids = [diff_one]
    if image_mutation == "extra":
        layers = [descriptor_one, descriptor_one]
    elif image_mutation == "reordered":
        layers = [descriptor_two, descriptor_one]
        diff_ids = [diff_one, diff_two]
        members[_blob_name(descriptor_two)] = raw_two
    elif image_mutation == "compression":
        bad_raw = b"not gzip"
        descriptor_one = _descriptor(
            bad_raw, "application/vnd.oci.image.layer.v1.tar+gzip"
        )
        layers = [descriptor_one]
        members = {
            name: value
            for name, value in members.items()
            if name == "oci-layout"
        }
        members[_blob_name(descriptor_one)] = bad_raw
    elif image_mutation == "size":
        layers = [{**descriptor_one, "size": descriptor_one["size"] + 1}]
    elif image_mutation == "missing":
        members.pop(_blob_name(descriptor_one))
    elif image_mutation == "blob":
        members[_blob_name(descriptor_one)] = raw_one[:-1] + b"x"
    elif image_mutation == "truncated":
        members[_blob_name(descriptor_one)] = raw_one[:-2]

    config = _json_bytes({
        "architecture": architecture,
        "os": "linux",
        "config": {"Labels": {"org.opencontainers.image.revision": source_sha}},
        "rootfs": {"type": "layers", "diff_ids": diff_ids},
    })
    config_media_type = (
        "application/json"
        if image_mutation == "config_media_type"
        else OCI_CONFIG
    )
    config_descriptor = _descriptor(config, config_media_type)
    members[_blob_name(config_descriptor)] = config

    manifest = _json_bytes({
        "schemaVersion": image_schema,
        "mediaType": OCI_MANIFEST,
        "config": config_descriptor,
        "layers": layers,
    })
    image_descriptor = _descriptor(
        manifest,
        OCI_MANIFEST,
        platform={"architecture": architecture, "os": "linux"},
    )
    members[_blob_name(image_descriptor)] = manifest

    outer_manifests = [image_descriptor]
    attestation_descriptor = None
    statement_descriptors: list[dict] = []
    if include_attestations:
        predicate_types = []
        if include_sbom:
            predicate_types.append(SBOM)
        if include_provenance:
            predicate_types.append(PROVENANCE)
        for predicate_type in predicate_types:
            actual_predicate_type = predicate_type
            if (
                attestation_mutation == "unsupported_predicate"
                and predicate_type == SBOM
            ):
                actual_predicate_type = "https://example.invalid/predicate"
            statement_subject = (
                "f" * 64
                if attestation_mutation == "statement_subject"
                and not statement_descriptors
                else None
            )
            mutation = None
            if predicate_type == SBOM and attestation_mutation in (
                "empty_predicate",
                "spdx_version",
            ):
                mutation = attestation_mutation
            elif predicate_type == PROVENANCE and attestation_mutation in (
                "slsa_schema",
                "slsa_revision",
                "slsa_resolved_dependency",
                "slsa_builder_dependency",
                "slsa_byproduct",
            ):
                mutation = attestation_mutation
            raw = _statement(
                actual_predicate_type,
                image_descriptor["digest"],
                source_sha,
                subject_digest=statement_subject,
                mutation=mutation,
            )
            annotations = {"in-toto.io/predicate-type": actual_predicate_type}
            if (
                attestation_mutation == "predicate_annotation"
                and not statement_descriptors
            ):
                annotations["in-toto.io/predicate-type"] = "https://example.invalid"
            media_type = (
                "application/json"
                if attestation_mutation == "layer_media_type"
                and not statement_descriptors
                else IN_TOTO
            )
            if (
                attestation_mutation == "malformed_statement"
                and not statement_descriptors
            ):
                raw = b"not json"
            statement_descriptor = _descriptor(
                raw, media_type, annotations=annotations
            )
            statement_descriptors.append(statement_descriptor)
            members[_blob_name(statement_descriptor)] = raw

        config_raw = b"{}"
        attestation_config = _descriptor(config_raw, OCI_EMPTY)
        members[_blob_name(attestation_config)] = config_raw
        subject = {
            field: image_descriptor[field]
            for field in ("mediaType", "digest", "size")
        }
        if attestation_mutation == "manifest_subject":
            subject["digest"] = "sha256:" + "e" * 64
        attestation = {
            "schemaVersion": 2,
            "mediaType": OCI_MANIFEST,
            "artifactType": (
                "application/example"
                if attestation_mutation == "artifact_type"
                else "application/vnd.docker.attestation.manifest.v1+json"
            ),
            "config": attestation_config,
            "layers": (
                []
                if attestation_mutation == "no_layers"
                else statement_descriptors
            ),
            "subject": subject,
        }
        attestation_raw = _json_bytes(attestation)
        annotations = {
            "vnd.docker.reference.type": "attestation-manifest",
            "vnd.docker.reference.digest": image_descriptor["digest"],
        }
        platform = {"architecture": "unknown", "os": "unknown"}
        if attestation_mutation == "reference_type":
            annotations["vnd.docker.reference.type"] = "other"
        elif attestation_mutation == "reference_digest":
            annotations["vnd.docker.reference.digest"] = "sha256:" + "d" * 64
        elif attestation_mutation == "platform":
            platform = {"architecture": "amd64", "os": "linux"}
        attestation_descriptor = _descriptor(
            attestation_raw,
            OCI_MANIFEST,
            annotations=annotations,
            platform=platform,
        )
        members[_blob_name(attestation_descriptor)] = attestation_raw
        outer_manifests.append(attestation_descriptor)

    outer = _json_bytes({
        "schemaVersion": 2,
        "mediaType": OCI_INDEX,
        "manifests": outer_manifests,
    })
    outer_descriptor = _descriptor(outer, OCI_INDEX)
    members[_blob_name(outer_descriptor)] = outer
    index = {
        "schemaVersion": 2,
        "mediaType": OCI_INDEX,
        "manifests": [image_descriptor if direct_root else outer_descriptor],
    }
    members["index.json"] = _json_bytes(index)

    if tamper_target == "config":
        members[_blob_name(config_descriptor)] = config[:-1] + b" "
    elif tamper_target == "image_manifest":
        members[_blob_name(image_descriptor)] = manifest[:-1] + b" "
    elif tamper_target == "attestation_manifest":
        assert attestation_descriptor
        name = _blob_name(attestation_descriptor)
        members[name] = members[name][:-1] + b" "
    elif tamper_target == "statement":
        name = _blob_name(statement_descriptors[0])
        members[name] = members[name][:-1] + b" "

    _write_archive(path, members)
    return config_descriptor["digest"], diff_ids


def _configure_run(
    monkeypatch,
    tmp_path,
    archive_path,
    image_id: str,
    diff_ids: list[str],
    source_sha: str,
):
    metadata = tmp_path / "metadata.json"
    metadata.write_text(json.dumps([{
        "Id": image_id,
        "RootFS": {"Layers": diff_ids},
    }]))
    output = tmp_path / "verification.json"
    monkeypatch.setattr(sys, "argv", [
        "verify_oci_export.py",
        "--archive", str(archive_path),
        "--metadata", str(metadata),
        "--source-sha", source_sha,
        "--output", str(output),
    ])
    return output


def _run_verifier(monkeypatch, tmp_path, **kwargs):
    source_sha = "a" * 40
    archive = tmp_path / "image.oci.tar"
    image_id, diff_ids = _build_archive(archive, source_sha, **kwargs)
    output = _configure_run(
        monkeypatch, tmp_path, archive, image_id, diff_ids, source_sha
    )
    return output


def _validator(name: str):
    return runpy.run_path(SCRIPT)[name]


def test_verifier_accepts_attested_nested_linux_amd64_export(
    monkeypatch, tmp_path
):
    output = _run_verifier(monkeypatch, tmp_path)
    with pytest.raises(SystemExit, match="0"):
        runpy.run_path(SCRIPT, run_name="__main__")
    document = json.loads(output.read_text())
    assert document["status"] == "PASS"
    assert document["schema"] == "codestra-oci-export-verification/v2"
    assert document["oci_layout_version"] == "1.0.0"
    assert document["oci_index_schema_version"] == 2
    assert document["oci_config_media_type"] == OCI_CONFIG
    assert document["attestation_count"] == 1
    assert document["attestation_predicate_types"] == sorted([SBOM, PROVENANCE])


@pytest.mark.parametrize(
    "field",
    [
        "name",
        "SPDXID",
        "downloadLocation",
        "filesAnalyzed",
        "licenseConcluded",
        "licenseDeclared",
        "copyrightText",
    ],
)
def test_spdx_package_requires_every_schema_field(field):
    predicate = _spdx_predicate()
    del predicate["packages"][0][field]
    with pytest.raises(ValueError, match="SPDX predicate package"):
        _validator("validate_spdx_predicate")(predicate)


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", 1),
        ("SPDXID", ""),
        ("downloadLocation", None),
        ("filesAnalyzed", "false"),
        ("licenseConcluded", ""),
        ("licenseDeclared", []),
        ("copyrightText", ""),
    ],
)
def test_spdx_package_rejects_malformed_required_field(field, value):
    predicate = _spdx_predicate()
    predicate["packages"][0][field] = value
    with pytest.raises(ValueError, match="SPDX predicate package"):
        _validator("validate_spdx_predicate")(predicate)


def test_spdx_package_rejects_malformed_checksum():
    predicate = _spdx_predicate()
    predicate["packages"][0]["checksums"] = [None]
    with pytest.raises(ValueError, match="checksum"):
        _validator("validate_spdx_predicate")(predicate)


def test_spdx_relationship_rejects_missing_required_field():
    predicate = _spdx_predicate()
    del predicate["relationships"][0]["relationshipType"]
    with pytest.raises(ValueError, match="relationship"):
        _validator("validate_spdx_predicate")(predicate)


@pytest.mark.parametrize(
    "collection,path",
    [
        (
            "resolved dependency",
            ("buildDefinition", "resolvedDependencies"),
        ),
        (
            "builder dependency",
            ("runDetails", "builder", "builderDependencies"),
        ),
        ("byproduct", ("runDetails", "byproducts")),
    ],
)
def test_slsa_resource_descriptor_collections_reject_null(collection, path):
    predicate = _slsa_predicate("a" * 40)
    target = predicate
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = [None]
    with pytest.raises(ValueError, match="not an object"):
        _validator("validate_slsa_provenance")(predicate, "a" * 40)


@pytest.mark.parametrize(
    "descriptor,match",
    [
        ({}, "no uri, digest, or content"),
        ({"uri": ""}, "uri is missing"),
        ({"digest": {}}, "digest must be a nonempty object"),
        ({"content": ""}, "content is missing"),
        ({"uri": "pkg:test", "annotations": []}, "annotations"),
        ({"digest": {"sha256": ""}}, "digest value"),
    ],
)
def test_slsa_resource_descriptor_rejects_invalid_structure(descriptor, match):
    predicate = _slsa_predicate("a" * 40)
    predicate["buildDefinition"]["resolvedDependencies"] = [descriptor]
    with pytest.raises(ValueError, match=match):
        _validator("validate_slsa_provenance")(predicate, "a" * 40)


def test_verifier_rejects_direct_manifest_without_attached_attestations(
    monkeypatch, tmp_path
):
    _run_verifier(monkeypatch, tmp_path, direct_root=True)
    with pytest.raises(ValueError, match="root descriptor is not an image index"):
        runpy.run_path(SCRIPT, run_name="__main__")


def test_verifier_rejects_wrong_platform(monkeypatch, tmp_path):
    _run_verifier(monkeypatch, tmp_path, architecture="arm64")
    with pytest.raises(ValueError, match="linux/amd64"):
        runpy.run_path(SCRIPT, run_name="__main__")


@pytest.mark.parametrize("mutation,match", [
    ("missing", "member missing: oci-layout"),
    ("version", "layout version is not 1.0.0"),
    ("malformed", "layout metadata is invalid JSON"),
])
def test_verifier_rejects_invalid_oci_layout(
    monkeypatch, tmp_path, mutation, match
):
    source_sha = "a" * 40
    archive_path = tmp_path / "image.oci.tar"
    image_id, diff_ids = _build_archive(archive_path, source_sha)
    members = _read_members(archive_path)
    if mutation == "missing":
        del members["oci-layout"]
    elif mutation == "version":
        members["oci-layout"] = _json_bytes({"imageLayoutVersion": "0.9.0"})
    else:
        members["oci-layout"] = b"not json"
    _write_archive(archive_path, members)
    _configure_run(
        monkeypatch, tmp_path, archive_path, image_id, diff_ids, source_sha
    )
    with pytest.raises(ValueError, match=match):
        runpy.run_path(SCRIPT, run_name="__main__")


@pytest.mark.parametrize("value", [None, 1, "2", 2.0, True])
def test_verifier_requires_integer_oci_index_schema_version_two(
    monkeypatch, tmp_path, value
):
    source_sha = "a" * 40
    archive_path = tmp_path / "image.oci.tar"
    image_id, diff_ids = _build_archive(archive_path, source_sha)
    members = _read_members(archive_path)
    index = json.loads(members["index.json"])
    if value is None:
        index.pop("schemaVersion")
    else:
        index["schemaVersion"] = value
    members["index.json"] = _json_bytes(index)
    _write_archive(archive_path, members)
    _configure_run(
        monkeypatch, tmp_path, archive_path, image_id, diff_ids, source_sha
    )
    with pytest.raises(ValueError, match="OCI index schemaVersion must be integer 2"):
        runpy.run_path(SCRIPT, run_name="__main__")


def test_verifier_requires_image_manifest_schema_version_two(
    monkeypatch, tmp_path
):
    _run_verifier(monkeypatch, tmp_path, image_schema=1)
    with pytest.raises(
        ValueError, match="OCI image manifest schemaVersion must be integer 2"
    ):
        runpy.run_path(SCRIPT, run_name="__main__")


def test_verifier_requires_oci_image_config_media_type(monkeypatch, tmp_path):
    _run_verifier(monkeypatch, tmp_path, image_mutation="config_media_type")
    with pytest.raises(
        ValueError, match="OCI image config descriptor media type is invalid"
    ):
        runpy.run_path(SCRIPT, run_name="__main__")


@pytest.mark.parametrize("kwargs,match", [
    ({"include_attestations": False}, "no attached attestation"),
    ({"include_sbom": False}, "missing the SPDX SBOM"),
    ({"include_provenance": False}, "missing the SLSA provenance"),
])
def test_verifier_requires_sbom_and_provenance_attestations(
    monkeypatch, tmp_path, kwargs, match
):
    _run_verifier(monkeypatch, tmp_path, **kwargs)
    with pytest.raises(ValueError, match=match):
        runpy.run_path(SCRIPT, run_name="__main__")


@pytest.mark.parametrize("mutation,match", [
    ("reference_type", "reference type is invalid"),
    ("reference_digest", "references the wrong image"),
    ("platform", "platform is not unknown/unknown"),
    ("artifact_type", "artifact type is invalid"),
    ("manifest_subject", "subject does not match image manifest"),
    ("statement_subject", "statement subject does not match image manifest"),
    ("predicate_annotation", "predicate annotation mismatch"),
    ("layer_media_type", "layer is not in-toto JSON"),
    ("malformed_statement", "statement is invalid JSON"),
    ("no_layers", "has no statement layers"),
    ("empty_predicate", "SPDX predicate version is not supported"),
    ("spdx_version", "SPDX predicate version is not supported"),
    ("slsa_schema", "SLSA provenance schema is incomplete"),
    ("slsa_revision", "source revision differs from exact head"),
    ("slsa_resolved_dependency", "resolved dependencies.*not an object"),
    ("slsa_builder_dependency", "builder dependencies.*not an object"),
    ("slsa_byproduct", "byproducts.*not an object"),
    ("unsupported_predicate", "predicate type is unsupported"),
])
def test_verifier_rejects_invalid_attestation_contract(
    monkeypatch, tmp_path, mutation, match
):
    _run_verifier(monkeypatch, tmp_path, attestation_mutation=mutation)
    with pytest.raises(ValueError, match=match):
        runpy.run_path(SCRIPT, run_name="__main__")


@pytest.mark.parametrize("mutation,match", [
    ("blob", "blob digest mismatch"),
    ("size", "descriptor size mismatch"),
    ("missing", "member missing"),
    ("extra", "counts differ"),
    ("reordered", "rootfs diff IDs"),
    ("truncated", "descriptor size mismatch"),
    ("compression", "compression is invalid"),
])
def test_verifier_rejects_layer_identity_failures(
    monkeypatch, tmp_path, mutation, match
):
    _run_verifier(monkeypatch, tmp_path, image_mutation=mutation)
    with pytest.raises(ValueError, match=match):
        runpy.run_path(SCRIPT, run_name="__main__")


@pytest.mark.parametrize(
    "target", ["config", "image_manifest", "attestation_manifest", "statement"]
)
def test_verifier_authenticates_every_image_and_attestation_blob(
    monkeypatch, tmp_path, target
):
    _run_verifier(monkeypatch, tmp_path, tamper_target=target)
    with pytest.raises(ValueError, match="blob digest mismatch"):
        runpy.run_path(SCRIPT, run_name="__main__")