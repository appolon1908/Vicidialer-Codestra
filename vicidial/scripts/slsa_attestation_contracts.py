"""Strict SLSA v1 value and source-material contracts for OCI attestations."""
from __future__ import annotations

import base64
import binascii
import re


SLSA_PROVENANCE_PREDICATE = "https://slsa.dev/provenance/v1"
SOURCE_REPOSITORY = "https://github.com/appolon1908-hue/Vicidialer-Codestra"
RESOURCE_DESCRIPTOR_STRING_FIELDS = (
    "name", "uri", "downloadLocation", "mediaType",
)
DIGEST_ALGORITHM_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9+._-]*$")
LOWERCASE_HEX_PATTERN = re.compile(r"^[0-9a-f]+$")


def require_nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is missing")
    return value


def validate_digest_set(value: object, label: str) -> None:
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{label} digest must be a nonempty object")
    fixed_lengths = {
        "sha1": {40},
        "sha256": {64},
        "sha512": {128},
        "gitCommit": {40, 64},
    }
    for algorithm, digest in value.items():
        algorithm = require_nonempty_string(
            algorithm, f"{label} digest algorithm"
        )
        if not DIGEST_ALGORITHM_PATTERN.fullmatch(algorithm):
            raise ValueError(f"{label} digest algorithm is invalid")
        digest = require_nonempty_string(digest, f"{label} digest value")
        if not LOWERCASE_HEX_PATTERN.fullmatch(digest):
            raise ValueError(f"{label} digest value is not lowercase hexadecimal")
        expected_lengths = fixed_lengths.get(algorithm)
        if expected_lengths is not None and len(digest) not in expected_lengths:
            raise ValueError(f"{label} digest length is invalid for {algorithm}")
        if expected_lengths is None and len(digest) % 2:
            raise ValueError(f"{label} digest value must contain complete bytes")


def validate_resource_descriptor(value: object, label: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not an object")
    for field in RESOURCE_DESCRIPTOR_STRING_FIELDS:
        if field in value:
            require_nonempty_string(value[field], f"{label} {field}")
    if "digest" in value:
        validate_digest_set(value["digest"], label)
    if "content" in value:
        content = require_nonempty_string(value["content"], f"{label} content")
        try:
            base64.b64decode(content, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"{label} content is not valid base64") from exc
    if "annotations" in value:
        annotations = value["annotations"]
        if not isinstance(annotations, dict) or not all(
            isinstance(key, str) and key for key in annotations
        ):
            raise ValueError(f"{label} annotations must be an object")
    if not any(field in value for field in ("uri", "digest", "content")):
        raise ValueError(f"{label} has no uri, digest, or content")


def validate_resource_descriptor_list(value: object, label: str) -> list[dict]:
    if not isinstance(value, list):
        raise ValueError(f"{label} are missing")
    for descriptor_index, descriptor in enumerate(value):
        validate_resource_descriptor(
            descriptor, f"{label}[{descriptor_index}]"
        )
    return value


def normalize_source_repository_uri(value: object) -> str:
    uri = require_nonempty_string(value, "SLSA provenance source URI")
    if uri.startswith("git+"):
        uri = uri[4:]
    if "#" in uri:
        uri = uri.rsplit("#", 1)[0]
    else:
        github_prefix = "https://github.com/"
        at_index = uri.rfind("@")
        if uri.startswith(github_prefix) and at_index >= len(github_prefix):
            uri = uri[:at_index]
    uri = uri.rstrip("/")
    if uri.endswith(".git"):
        uri = uri[:-4]
    return uri


def source_revision_from_uri(value: object) -> str | None:
    uri = require_nonempty_string(value, "SLSA provenance source URI")
    if uri.startswith("git+"):
        uri = uri[4:]
    if "#" in uri:
        return uri.rsplit("#", 1)[1]
    github_prefix = "https://github.com/"
    at_index = uri.rfind("@")
    if uri.startswith(github_prefix) and at_index >= len(github_prefix):
        return uri[at_index + 1:]
    return None


def validate_exact_source_descriptor(
    value: object, label: str, source_sha: str
) -> dict:
    validate_resource_descriptor(value, label)
    descriptor = value
    uri = descriptor.get("uri")
    if normalize_source_repository_uri(uri) != SOURCE_REPOSITORY:
        raise ValueError(f"{label} repository is invalid")
    digest = descriptor.get("digest")
    if not isinstance(digest, dict):
        raise ValueError(f"{label} digest is missing")
    expected_algorithm = "sha256" if len(source_sha) == 64 else "sha1"
    if (
        digest.get(expected_algorithm) != source_sha
        and digest.get("gitCommit") != source_sha
    ):
        raise ValueError(f"{label} differs from exact head")
    uri_revision = source_revision_from_uri(uri)
    if uri_revision is not None and len(uri_revision) in {40, 64}:
        if not LOWERCASE_HEX_PATTERN.fullmatch(uri_revision):
            raise ValueError(f"{label} URI revision is invalid")
        if uri_revision != source_sha:
            raise ValueError(f"{label} URI revision differs from exact head")
    return descriptor


def validate_exact_source_dependency(
    dependencies: list[dict], source_sha: str
) -> dict:
    if len(source_sha) not in {40, 64} or not LOWERCASE_HEX_PATTERN.fullmatch(
        source_sha
    ):
        raise ValueError("SLSA provenance exact source SHA is invalid")
    matches = [
        dependency
        for dependency in dependencies
        if isinstance(dependency.get("uri"), str)
        and normalize_source_repository_uri(dependency["uri"]) == SOURCE_REPOSITORY
    ]
    if len(matches) != 1:
        raise ValueError(
            "SLSA provenance must contain exactly one resolved source dependency"
        )
    return validate_exact_source_descriptor(
        matches[0], "SLSA provenance source dependency", source_sha
    )


def validate_external_source_assertions(
    external_parameters: dict, source_sha: str
) -> None:
    config_source = external_parameters.get("configSource")
    if config_source is not None:
        validate_exact_source_descriptor(
            config_source, "SLSA external config source", source_sha
        )
    request = external_parameters.get("request")
    if request is None:
        return
    if not isinstance(request, dict):
        raise ValueError("SLSA external request is invalid")
    root = request.get("root")
    if root is None:
        return
    if not isinstance(root, dict):
        raise ValueError("SLSA external request root is invalid")
    root_config_source = root.get("configSource")
    if root_config_source is not None:
        validate_exact_source_descriptor(
            root_config_source, "SLSA root config source", source_sha
        )


def validate_slsa_provenance(predicate: object, source_sha: str) -> None:
    if len(source_sha) not in {40, 64} or not LOWERCASE_HEX_PATTERN.fullmatch(
        source_sha
    ):
        raise ValueError("SLSA provenance exact source SHA is invalid")
    if not isinstance(predicate, dict):
        raise ValueError("SLSA provenance predicate is not an object")
    definition = predicate.get("buildDefinition")
    details = predicate.get("runDetails")
    if not isinstance(definition, dict) or not isinstance(details, dict):
        raise ValueError("SLSA provenance schema is incomplete")
    require_nonempty_string(definition.get("buildType"), "SLSA build type")
    dependencies = validate_resource_descriptor_list(
        definition.get("resolvedDependencies"), "SLSA resolved dependencies"
    )
    validate_exact_source_dependency(dependencies, source_sha)
    external_parameters = definition.get("externalParameters")
    if not isinstance(external_parameters, dict):
        raise ValueError("SLSA external parameters are missing")
    validate_external_source_assertions(external_parameters, source_sha)
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
    if buildkit is not None and not isinstance(buildkit, dict):
        raise ValueError("SLSA BuildKit metadata is invalid")
    vcs = buildkit.get("vcs") if isinstance(buildkit, dict) else None
    if vcs is not None:
        if not isinstance(vcs, dict) or vcs.get("revision") != source_sha:
            raise ValueError("SLSA provenance source revision differs from exact head")
        if (
            "source" in vcs
            and normalize_source_repository_uri(vcs["source"])
            != SOURCE_REPOSITORY
        ):
            raise ValueError("SLSA provenance source repository is invalid")
