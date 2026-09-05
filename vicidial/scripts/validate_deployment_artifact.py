#!/usr/bin/env python3
"""Validate the release artifact used by production Compose."""
import argparse
import json
import re
from pathlib import Path

REPOSITORY = "ghcr.io/appolon1908-hue/vicidialer-codestra"
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def validate(manifest: dict, compose: str) -> str:
    required = {"artifact_kind", "artifact_reference", "artifact_digest", "source_sha"}
    if not required <= manifest.keys():
        raise ValueError("release manifest missing deployment identity")
    if manifest["artifact_kind"] != "oci-image":
        raise ValueError("release artifact is not an OCI image")
    digest = manifest["artifact_digest"]
    reference = manifest["artifact_reference"]
    if not DIGEST.fullmatch(digest) or reference != f"{REPOSITORY}@{digest}":
        raise ValueError("artifact reference is not the permitted immutable repository")
    if not re.fullmatch(r"[0-9a-f]{40}", manifest["source_sha"]):
        raise ValueError("release source SHA is invalid")
    marker = "image: ${VICIDIAL_ADAPTER_IMAGE:?complete verified GHCR artifact reference required}"
    if marker not in compose:
        raise ValueError("production Compose does not consume the complete artifact reference")
    return reference


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-manifest", type=Path, required=True)
    parser.add_argument("--compose", type=Path, required=True)
    args = parser.parse_args()
    reference = validate(
        json.loads(args.release_manifest.read_text(encoding="utf-8")),
        args.compose.read_text(encoding="utf-8"),
    )
    print(f"VICIDIAL_ADAPTER_IMAGE={reference}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
