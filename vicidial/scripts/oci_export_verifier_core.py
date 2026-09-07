#!/usr/bin/env python3.11
"""Prove an OCI export is the tested image and carries required attestations."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path


OCI_IMAGE_INDEX = "application/vnd.oci.image.index.v1+json"
OCI_IMAGE_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_IMAGE_CONFIG = "application/vnd.oci.image.config.v1+json"
OCI_EMPTY_CONFIG = "application/vnd.oci.empty.v1+json"
OCI_LAYER_TAR = "application/vnd.oci.image.layer.v1.tar"
OCI_LAYER_GZIP = "application/vnd.oci.image.layer.v1.tar+gzip"
DOCKER_LAYER_TAR = "application/vnd.docker.image.rootfs.diff.tar"
DOCKER_LAYER_GZIP = "application/vnd.docker.image.rootfs.diff.tar.gzip"
SUPPORTED_UNCOMPRESSED_LAYER_MEDIA_TYPES = frozenset(
    {OCI_LAYER_TAR, DOCKER_LAYER_TAR}
)
SUPPORTED_GZIP_LAYER_MEDIA_TYPES = frozenset(
    {OCI_LAYER_GZIP, DOCKER_LAYER_GZIP}
)
ATTESTATION_ARTIFACT = "application/vnd.docker.attestation.manifest.v1+json"
IN_TOTO_MEDIA_TYPE = "application/vnd.in-toto+json"
REFERENCE_TYPE = "vnd.docker.reference.type"
REFERENCE_DIGEST = "vnd.docker.reference.digest"
ATTESTATION_REFERENCE_TYPE = "attestation-manifest"
PREDICATE_ANNOTATION = "in-toto.io/predicate-type"
SBOM_PREDICATE = "https://spdx.dev/Document"
SLSA_PROVENANCE_PREDICATE = "https://slsa.dev/provenance/v1"
LINUX_AMD64 = {"architecture": "amd64", "os": "linux"}
UNKNOWN_PLATFORM = {"architecture": "unknown", "os": "unknown"}
SPDX_PACKAGE_REQUIRED_STRING_FIELDS = (
    "name",
    "SPDXID",
    "downloadLocation",
    "licenseConcluded",
    "licenseDeclared",
    "copyrightText",
)
RESOURCE_DESCRIPTOR_STRING_FIELDS = (
    "name",
    "uri",
    "downloadLocation",
    "mediaType",
)


def member_bytes(archive: tarfile.TarFile, name: str) -> bytes:
    try:
        member = archive.extractfile(name)
    except KeyError as exc:
        raise ValueError(f"OCI member missing: {name}") from exc
    if member is None:
        raise ValueError(f"OCI member missing: {name}")
    return member.read()


def member_json(archive: tarfile.TarFile, name: str) -> dict:
    try:
        document = json.loads(member_bytes(archive, name))
    except json.JSONDecodeError as exc:
        raise ValueError(f"OCI JSON member is invalid: {name}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"OCI JSON member is not an object: {name}")
    return document


def require_schema_version(document: dict, label: str) -> None:
    value = document.get("schemaVersion")
    if type(value) is not int or value != 2:
        raise ValueError(f"{label} schemaVersion must be integer 2")


def require_media_type(descriptor: object, expected: str, label: str) -> None:
    if not isinstance(descriptor, dict):
        raise ValueError(f"{label} is missing")
    if descriptor.get("mediaType") != expected:
        raise ValueError(f"{label} media type is invalid")


def require_nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is missing")
    return value


def descriptor_bytes(archive: tarfile.TarFile, descriptor: dict) -> bytes:
    if not isinstance(descriptor, dict):
        raise ValueError("OCI descriptor is not an object")
    digest = descriptor.get("digest")
    size = descriptor.get("size")
    if not isinstance(digest, str) or not isinstance(size, int) or size < 0:
        raise ValueError("OCI descriptor digest or size missing")
    parts = digest.split(":", 1)
    if len(parts) != 2:
        raise ValueError("OCI digest is not SHA-256")
    algorithm, value = parts
    if algorithm != "sha256" or len(value) != 64:
        raise ValueError("OCI digest is not SHA-256")
    raw = member_bytes(archive, f"blobs/sha256/{value}")
    if len(raw) != size:
        raise ValueError("OCI descriptor size mismatch")
    if hashlib.sha256(raw).hexdigest() != value:
        raise ValueError("OCI blob digest mismatch")
    return raw


def descriptor_json(archive: tarfile.TarFile, descriptor: dict) -> dict:
    try:
        document = json.loads(descriptor_bytes(archive, descriptor))
    except json.JSONDecodeError as exc:
        raise ValueError("OCI descriptor payload is invalid JSON") from exc
    if not isinstance(document, dict):
        raise ValueError("OCI descriptor JSON is not an object")
    return document


def layer_diff_id(raw: bytes, media_type: str) -> str:
    if media_type in SUPPORTED_GZIP_LAYER_MEDIA_TYPES:
        try:
            payload = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        except (OSError, EOFError) as exc:
            raise ValueError("OCI layer gzip compression is invalid") from exc
    elif media_type in SUPPORTED_UNCOMPRESSED_LAYER_MEDIA_TYPES:
        payload = raw
    else:
        raise ValueError(f"unsupported OCI layer media type: {media_type}")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def require_subject_descriptor(subject: object, target: dict) -> None:
    if not isinstance(subject, dict):
        raise ValueError("attestation subject descriptor is missing")
    for field in ("mediaType", "digest", "size"):
        if subject.get(field) != target.get(field):
            raise ValueError("attestation subject does not match image manifest")


def validate_spdx_checksum(checksum: object, label: str) -> None:
    if not isinstance(checksum, dict):
        raise ValueError(f"{label} is not an object")
    require_nonempty_string(checksum.get("algorithm"), f"{label} algorithm")
    value = require_nonempty_string(
        checksum.get("checksumValue"), f"{label} value"
    )
    if any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{label} value is not lowercase hexadecimal")


def validate_spdx_package(package: object, index: int) -> None:
    label = f"SPDX predicate package[{index}]"
    if not isinstance(package, dict):
        raise ValueError(f"{label} is not an object")
    for field in SPDX_PACKAGE_REQUIRED_STRING_FIELDS:
        require_nonempty_string(package.get(field), f"{label} {field}")
    if type(package.get("filesAnalyzed")) is not bool:
        raise ValueError(f"{label} filesAnalyzed must be boolean")
    checksums = package.get("checksums")
    if checksums is not None:
        if not isinstance(checksums, list):
            raise ValueError(f"{label} checksums must be a list")
        for checksum_index, checksum in enumerate(checksums):
            validate_spdx_checksum(
                checksum, f"{label} checksum[{checksum_index}]"
            )


def validate_spdx_relationship(relationship: object, index: int) -> None:
    label = f"SPDX predicate relationship[{index}]"
    if not isinstance(relationship, dict):
        raise ValueError(f"{label} is not an object")
    for field in ("spdxElementId", "relationshipType", "relatedSpdxElement"):
        require_nonempty_string(relationship.get(field), f"{label} {field}")


def validate_spdx_predicate(predicate: object) -> None:
    if not isinstance(predicate, dict):
        raise ValueError("SPDX predicate is not an object")
    if predicate.get("spdxVersion") != "SPDX-2.3":
        raise ValueError("SPDX predicate version is not supported")
    if predicate.get("dataLicense") != "CC0-1.0":
        raise ValueError("SPDX predicate data license is invalid")
    if predicate.get("SPDXID") != "SPDXRef-DOCUMENT":
        raise ValueError("SPDX predicate document ID is invalid")
    require_nonempty_string(predicate.get("name"), "SPDX predicate name")
    require_nonempty_string(
        predicate.get("documentNamespace"), "SPDX predicate namespace"
    )
    creation = predicate.get("creationInfo")
    if not isinstance(creation, dict):
        raise ValueError("SPDX predicate creation info is missing")
    creators = creation.get("creators")
    if not isinstance(creators, list) or not creators or not all(
        isinstance(value, str) and value.strip() for value in creators
    ):
        raise ValueError("SPDX predicate creators are missing")
    require_nonempty_string(creation.get("created"), "SPDX predicate creation time")
    packages = predicate.get("packages")
    if not isinstance(packages, list) or not packages:
        raise ValueError("SPDX predicate packages are missing")
    for package_index, package in enumerate(packages):
        validate_spdx_package(package, package_index)
    relationships = predicate.get("relationships")
    if not isinstance(relationships, list):
        raise ValueError("SPDX predicate relationships are missing")
    for relationship_index, relationship in enumerate(relationships):
        validate_spdx_relationship(relationship, relationship_index)


def validate_digest_set(value: object, label: str) -> None:
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{label} digest must be a nonempty object")
    for algorithm, digest in value.items():
        require_nonempty_string(algorithm, f"{label} digest algorithm")
        require_nonempty_string(digest, f"{label} digest value")


def validate_resource_descriptor(value: object, label: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not an object")
    for field in RESOURCE_DESCRIPTOR_STRING_FIELDS:
        if field in value:
            require_nonempty_string(value[field], f"{label} {field}")
    if "digest" in value:
        validate_digest_set(value["digest"], label)
    if "content" in value:
        require_nonempty_string(value["content"], f"{label} content")
    if "annotations" in value:
        annotations = value["annotations"]
        if not isinstance(annotations, dict) or not all(
            isinstance(key, str) and key for key in annotations
        ):
            raise ValueError(f"{label} annotations must be an object")
    if not any(field in value for field in ("uri", "digest", "content")):
        raise ValueError(f"{label} has no uri, digest, or content")


def validate_resource_descriptor_list(value: object, label: str) -> None:
    if not isinstance(value, list):
        raise ValueError(f"{label} are missing")
    for descriptor_index, descriptor in enumerate(value):
        validate_resource_descriptor(
            descriptor, f"{label}[{descriptor_index}]"
        )


def validate_slsa_provenance(predicate: object, source_sha: str) -> None:
    if not isinstance(predicate, dict):
        raise ValueError("SLSA provenance predicate is not an object")
    definition = predicate.get("buildDefinition")
    details = predicate.get("runDetails")
    if not isinstance(definition, dict) or not isinstance(details, dict):
        raise ValueError("SLSA provenance schema is incomplete")
    require_nonempty_string(definition.get("buildType"), "SLSA build type")
    validate_resource_descriptor_list(
        definition.get("resolvedDependencies"), "SLSA resolved dependencies"
    )
    if not isinstance(definition.get("externalParameters"), dict):
        raise ValueError("SLSA external parameters are missing")
    if not isinstance(definition.get("internalParameters"), dict):
        raise ValueError("SLSA internal parameters are missing")
    builder = details.get("builder")
    metadata = details.get("metadata")
    if not isinstance(builder, dict) or not isinstance(metadata, dict):
        raise ValueError("SLSA run details are incomplete")
    require_nonempty_string(builder.get("id"), "SLSA builder ID")
    if "builderDependencies" in builder:
        validate_resource_descriptor_list(
            builder["builderDependencies"], "SLSA builder dependencies"
        )
    version = builder.get("version")
    if version is not None and (
        not isinstance(version, dict)
        or not all(
            isinstance(key, str)
            and key
            and isinstance(item, str)
            and item
            for key, item in version.items()
        )
    ):
        raise ValueError("SLSA builder version is invalid")
    require_nonempty_string(metadata.get("invocationId"), "SLSA invocation ID")
    require_nonempty_string(metadata.get("startedOn"), "SLSA start time")
    require_nonempty_string(metadata.get("finishedOn"), "SLSA finish time")
    if "byproducts" in details:
        validate_resource_descriptor_list(
            details["byproducts"], "SLSA byproducts"
        )
    buildkit = metadata.get("buildkit_metadata")
    vcs = buildkit.get("vcs") if isinstance(buildkit, dict) else None
    if not isinstance(vcs, dict) or vcs.get("revision") != source_sha:
        raise ValueError("SLSA provenance source revision differs from exact head")


def verify_statement(
    raw: bytes,
    descriptor: dict,
    image_digest: str,
    source_sha: str,
) -> str:
    if descriptor.get("mediaType") != IN_TOTO_MEDIA_TYPE:
        raise ValueError("attestation layer is not in-toto JSON")
    try:
        statement = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("attestation statement is invalid JSON") from exc
    if not isinstance(statement, dict):
        raise ValueError("attestation statement is not an object")
    statement_type = statement.get("_type")
    if not isinstance(statement_type, str) or not statement_type.startswith(
        "https://in-toto.io/Statement/"
    ):
        raise ValueError("attestation statement type is invalid")
    predicate_type = statement.get("predicateType")
    if predicate_type not in (SBOM_PREDICATE, SLSA_PROVENANCE_PREDICATE):
        raise ValueError("attestation predicate type is unsupported")
    annotations = descriptor.get("annotations")
    if not isinstance(annotations, dict) or annotations.get(
        PREDICATE_ANNOTATION
    ) != predicate_type:
        raise ValueError("attestation predicate annotation mismatch")
    expected = image_digest.split(":", 1)[1]
    subjects = statement.get("subject")
    if not isinstance(subjects, list) or not any(
        isinstance(subject, dict)
        and isinstance(subject.get("name"), str)
        and subject["name"]
        and isinstance(subject.get("digest"), dict)
        and subject["digest"].get("sha256") == expected
        for subject in subjects
    ):
        raise ValueError("attestation statement subject does not match image manifest")
    predicate = statement.get("predicate")
    if predicate_type == SBOM_PREDICATE:
        validate_spdx_predicate(predicate)
    else:
        validate_slsa_provenance(predicate, source_sha)
    return predicate_type


def verify_attestations(
    archive: tarfile.TarFile,
    outer: dict,
    image_descriptor: dict,
    source_sha: str,
) -> tuple[list[str], list[str]]:
    manifests = outer.get("manifests")
    if not isinstance(manifests, list):
        raise ValueError("OCI root is not a nested image index")
    image_digest = image_descriptor.get("digest")
    attestation_descriptors = [
        descriptor for descriptor in manifests if descriptor is not image_descriptor
    ]
    if not attestation_descriptors:
        raise ValueError("OCI export has no attached attestation manifest")

    manifest_digests: list[str] = []
    predicate_types: set[str] = set()
    for descriptor in attestation_descriptors:
        if descriptor.get("mediaType") != OCI_IMAGE_MANIFEST:
            raise ValueError("attestation descriptor media type is invalid")
        if descriptor.get("platform") != UNKNOWN_PLATFORM:
            raise ValueError("attestation descriptor platform is not unknown/unknown")
        annotations = descriptor.get("annotations")
        if not isinstance(annotations, dict):
            raise ValueError("attestation descriptor annotations are missing")
        if annotations.get(REFERENCE_TYPE) != ATTESTATION_REFERENCE_TYPE:
            raise ValueError("attestation descriptor reference type is invalid")
        if annotations.get(REFERENCE_DIGEST) != image_digest:
            raise ValueError("attestation descriptor references the wrong image")
        attestation = descriptor_json(archive, descriptor)
        require_schema_version(attestation, "attestation manifest")
        if attestation.get("mediaType") != OCI_IMAGE_MANIFEST:
            raise ValueError("attestation manifest media type is invalid")
        if attestation.get("artifactType") != ATTESTATION_ARTIFACT:
            raise ValueError("attestation manifest artifact type is invalid")
        require_subject_descriptor(attestation.get("subject"), image_descriptor)
        config_descriptor = attestation.get("config")
        if not isinstance(config_descriptor, dict):
            raise ValueError("attestation config descriptor is missing")
        if config_descriptor.get("mediaType") != OCI_EMPTY_CONFIG:
            raise ValueError("attestation config is not the OCI empty descriptor")
        config = descriptor_bytes(archive, config_descriptor)
        if config != b"{}":
            raise ValueError("attestation config is not empty JSON")
        layers = attestation.get("layers")
        if not isinstance(layers, list) or not layers:
            raise ValueError("attestation manifest has no statement layers")
        for layer in layers:
            raw = descriptor_bytes(archive, layer)
            predicate_types.add(
                verify_statement(raw, layer, image_digest, source_sha)
            )
        manifest_digests.append(descriptor["digest"])

    required = {SBOM_PREDICATE, SLSA_PROVENANCE_PREDICATE}
    if predicate_types != required:
        if SBOM_PREDICATE not in predicate_types:
            raise ValueError("OCI export is missing the SPDX SBOM attestation")
        raise ValueError("OCI export is missing the SLSA provenance attestation")
    return sorted(manifest_digests), sorted(predicate_types)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    metadata_documents = json.loads(args.metadata.read_text(encoding="utf-8"))
    if not isinstance(metadata_documents, list) or len(metadata_documents) != 1:
        raise ValueError("Docker image metadata must contain exactly one image")
    metadata = metadata_documents[0]
    if not isinstance(metadata, dict):
        raise ValueError("Docker image metadata is invalid")
    expected_config = metadata.get("Id")
    if not isinstance(expected_config, str) or not expected_config.startswith("sha256:"):
        raise ValueError("Docker image ID is missing")

    with tarfile.open(args.archive, "r") as archive:
        try:
            layout = json.loads(member_bytes(archive, "oci-layout"))
        except json.JSONDecodeError as exc:
            raise ValueError("OCI layout metadata is invalid JSON") from exc
        if not isinstance(layout, dict) or layout.get("imageLayoutVersion") != "1.0.0":
            raise ValueError("OCI image layout version is not 1.0.0")

        index = member_json(archive, "index.json")
        require_schema_version(index, "OCI index")
        roots = index.get("manifests")
        if not isinstance(roots, list) or len(roots) != 1:
            raise ValueError("OCI archive does not contain exactly one root index")
        root_descriptor = roots[0]
        if root_descriptor.get("mediaType") != OCI_IMAGE_INDEX:
            raise ValueError("OCI root descriptor is not an image index")
        outer = descriptor_json(archive, root_descriptor)
        require_schema_version(outer, "OCI root index")
        if outer.get("mediaType") != OCI_IMAGE_INDEX:
            raise ValueError("OCI root is not a nested image index")
        outer_manifests = outer.get("manifests")
        if not isinstance(outer_manifests, list):
            raise ValueError("OCI root is not a nested image index")
        candidates = [
            item
            for item in outer_manifests
            if item.get("platform") == LINUX_AMD64
            and not (
                isinstance(item.get("annotations"), dict)
                and item["annotations"].get(REFERENCE_TYPE)
                == ATTESTATION_REFERENCE_TYPE
            )
        ]
        if len(candidates) != 1:
            raise ValueError("OCI archive does not contain one linux/amd64 image")
        manifest_descriptor = candidates[0]
        if manifest_descriptor.get("mediaType") != OCI_IMAGE_MANIFEST:
            raise ValueError("OCI image descriptor media type is invalid")
        manifest = descriptor_json(archive, manifest_descriptor)
        require_schema_version(manifest, "OCI image manifest")
        if manifest.get("mediaType") not in (None, OCI_IMAGE_MANIFEST):
            raise ValueError("OCI image manifest media type is invalid")

        attestation_manifest_digests, attestation_predicate_types = (
            verify_attestations(
                archive, outer, manifest_descriptor, args.source_sha
            )
        )

        config_descriptor = manifest.get("config")
        require_media_type(
            config_descriptor,
            OCI_IMAGE_CONFIG,
            "OCI image config descriptor",
        )
        if config_descriptor.get("digest") != expected_config:
            raise ValueError("OCI config digest differs from tested Docker image ID")
        config = descriptor_json(archive, config_descriptor)
        if (config.get("architecture"), config.get("os")) != ("amd64", "linux"):
            raise ValueError("OCI archive does not contain one linux/amd64 image")
        labels = config.get("config", {}).get("Labels", {})
        if labels.get("org.opencontainers.image.revision") != args.source_sha:
            raise ValueError("OCI source revision differs from exact head")

        layers = manifest.get("layers")
        diff_ids = config.get("rootfs", {}).get("diff_ids")
        if not isinstance(layers, list) or not isinstance(diff_ids, list):
            raise ValueError("OCI layer or rootfs identity is missing")
        if len(layers) != len(diff_ids):
            raise ValueError("OCI layer and rootfs diff-ID counts differ")
        computed_diff_ids = [
            layer_diff_id(descriptor_bytes(archive, layer), layer.get("mediaType", ""))
            for layer in layers
        ]
        if computed_diff_ids != diff_ids:
            raise ValueError("OCI layer content differs from rootfs diff IDs")
        tested_diff_ids = metadata.get("RootFS", {}).get("Layers")
        if tested_diff_ids != diff_ids:
            raise ValueError("OCI rootfs differs from tested Docker image")

        layout_sha256 = hashlib.sha256(member_bytes(archive, "oci-layout")).hexdigest()
        index_sha256 = hashlib.sha256(member_bytes(archive, "index.json")).hexdigest()

    document = {
        "schema": "codestra-oci-export-verification/v2",
        "source_sha": args.source_sha,
        "docker_image_id": expected_config,
        "oci_layout_version": layout["imageLayoutVersion"],
        "oci_index_schema_version": index["schemaVersion"],
        "oci_manifest_digest": manifest_descriptor["digest"],
        "oci_config_digest": config_descriptor["digest"],
        "oci_config_media_type": config_descriptor["mediaType"],
        "oci_layer_digests": [item["digest"] for item in layers],
        "oci_layer_media_types": [item["mediaType"] for item in layers],
        "rootfs_diff_ids": diff_ids,
        "attestation_count": len(attestation_manifest_digests),
        "attestation_manifest_digests": attestation_manifest_digests,
        "attestation_predicate_types": attestation_predicate_types,
        "oci_layout_sha256": layout_sha256,
        "index_sha256": index_sha256,
        "archive_sha256": hashlib.sha256(args.archive.read_bytes()).hexdigest(),
        "status": "PASS",
    }
    args.output.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print("OCI_EXPORT_VERIFICATION=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())