"""Strict SPDX 2.3 value contracts for OCI SBOM attestations."""
from __future__ import annotations

import re


SBOM_PREDICATE = "https://spdx.dev/Document"
SPDX_PACKAGE_REQUIRED_STRING_FIELDS = (
    "name",
    "SPDXID",
    "downloadLocation",
    "licenseConcluded",
    "licenseDeclared",
    "copyrightText",
)
SPDX_ID_COMPONENT = r"[A-Za-z0-9.-]+"
SPDX_DOCUMENT_ID_COMPONENT = r"[A-Za-z0-9.+-]+"
SPDX_LOCAL_ID_PATTERN = re.compile(rf"^SPDXRef-{SPDX_ID_COMPONENT}$")
SPDX_EXTERNAL_ID_PATTERN = re.compile(
    rf"^DocumentRef-{SPDX_DOCUMENT_ID_COMPONENT}:"
    rf"SPDXRef-{SPDX_ID_COMPONENT}$"
)
SPDX_SPECIAL_ELEMENT_IDS = frozenset({"NONE", "NOASSERTION"})
SPDX_CHECKSUM_LENGTHS = {
    "ADLER32": frozenset({8}),
    "BLAKE2b-256": frozenset({64}),
    "BLAKE2b-384": frozenset({96}),
    "BLAKE2b-512": frozenset({128}),
    "BLAKE3": frozenset({64}),
    "MD2": frozenset({32}),
    "MD4": frozenset({32}),
    "MD5": frozenset({32}),
    "MD6": frozenset({56, 64, 96, 128}),
    "SHA1": frozenset({40}),
    "SHA224": frozenset({56}),
    "SHA256": frozenset({64}),
    "SHA384": frozenset({96}),
    "SHA512": frozenset({128}),
    "SHA3-256": frozenset({64}),
    "SHA3-384": frozenset({96}),
    "SHA3-512": frozenset({128}),
}
SPDX_RELATIONSHIP_TYPES = frozenset(
    {
        "AMENDS", "ANCESTOR_OF", "BUILD_DEPENDENCY_OF", "BUILD_TOOL_OF",
        "CONTAINED_BY", "CONTAINS", "COPY_OF", "DATA_FILE_OF",
        "DEPENDENCY_MANIFEST_OF", "DEPENDENCY_OF", "DEPENDS_ON",
        "DESCENDANT_OF", "DESCRIBED_BY", "DESCRIBES",
        "DEV_DEPENDENCY_OF", "DEV_TOOL_OF", "DISTRIBUTION_ARTIFACT",
        "DOCUMENTATION_OF", "DYNAMIC_LINK", "EXAMPLE_OF",
        "EXPANDED_FROM_ARCHIVE", "FILE_ADDED", "FILE_DELETED",
        "FILE_MODIFIED", "GENERATED_FROM", "GENERATES",
        "HAS_PREREQUISITE", "METAFILE_OF", "OPTIONAL_COMPONENT_OF",
        "OPTIONAL_DEPENDENCY_OF", "OTHER", "PACKAGE_OF",
        "PATCH_APPLIED", "PATCH_FOR", "PREREQUISITE_FOR",
        "PROVIDED_DEPENDENCY_OF", "REQUIREMENT_DESCRIPTION_FOR",
        "RUNTIME_DEPENDENCY_OF", "SPECIFICATION_FOR", "STATIC_LINK",
        "TEST_CASE_OF", "TEST_DEPENDENCY_OF", "TEST_OF", "TEST_TOOL_OF",
        "VARIANT_OF",
    }
)
LOWERCASE_HEX_PATTERN = re.compile(r"^[0-9a-f]+$")


def require_nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is missing")
    return value


def validate_spdx_element_id(
    value: object,
    label: str,
    *,
    allow_external: bool,
    allow_special: bool,
) -> str:
    identifier = require_nonempty_string(value, label)
    valid = bool(SPDX_LOCAL_ID_PATTERN.fullmatch(identifier))
    if allow_external:
        valid = valid or bool(SPDX_EXTERNAL_ID_PATTERN.fullmatch(identifier))
    if allow_special:
        valid = valid or identifier in SPDX_SPECIAL_ELEMENT_IDS
    if not valid:
        raise ValueError(f"{label} is not a valid SPDX element identifier")
    return identifier


def validate_spdx_checksum(checksum: object, label: str) -> None:
    if not isinstance(checksum, dict):
        raise ValueError(f"{label} is not an object")
    algorithm = require_nonempty_string(
        checksum.get("algorithm"), f"{label} algorithm"
    )
    if algorithm not in SPDX_CHECKSUM_LENGTHS:
        raise ValueError(f"{label} algorithm is not supported by SPDX 2.3")
    value = require_nonempty_string(
        checksum.get("checksumValue"), f"{label} value"
    )
    if not LOWERCASE_HEX_PATTERN.fullmatch(value):
        raise ValueError(f"{label} value is not lowercase hexadecimal")
    if len(value) not in SPDX_CHECKSUM_LENGTHS[algorithm]:
        raise ValueError(f"{label} value length is invalid for {algorithm}")


def validate_spdx_package(package: object, index: int) -> None:
    label = f"SPDX predicate package[{index}]"
    if not isinstance(package, dict):
        raise ValueError(f"{label} is not an object")
    for field in SPDX_PACKAGE_REQUIRED_STRING_FIELDS:
        require_nonempty_string(package.get(field), f"{label} {field}")
    validate_spdx_element_id(
        package.get("SPDXID"),
        f"{label} SPDXID",
        allow_external=False,
        allow_special=False,
    )
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
    validate_spdx_element_id(
        relationship.get("spdxElementId"),
        f"{label} spdxElementId",
        allow_external=True,
        allow_special=False,
    )
    validate_spdx_element_id(
        relationship.get("relatedSpdxElement"),
        f"{label} relatedSpdxElement",
        allow_external=True,
        allow_special=True,
    )
    relationship_type = require_nonempty_string(
        relationship.get("relationshipType"), f"{label} relationshipType"
    )
    if relationship_type not in SPDX_RELATIONSHIP_TYPES:
        raise ValueError(f"{label} relationshipType is not supported by SPDX 2.3")


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
    package_ids: set[str] = set()
    for package_index, package in enumerate(packages):
        validate_spdx_package(package, package_index)
        package_id = package["SPDXID"]
        if package_id in package_ids:
            raise ValueError("SPDX predicate package IDs are not unique")
        package_ids.add(package_id)
    relationships = predicate.get("relationships")
    if not isinstance(relationships, list):
        raise ValueError("SPDX predicate relationships are missing")
    for relationship_index, relationship in enumerate(relationships):
        validate_spdx_relationship(relationship, relationship_index)
