#!/usr/bin/env python3.11
"""Prove an OCI export is the same platform image tested in Docker."""
from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
from pathlib import Path


def member_json(archive: tarfile.TarFile, name: str) -> dict:
    member = archive.extractfile(name)
    if member is None:
        raise ValueError(f"OCI member missing: {name}")
    return json.load(member)


def blob_json(archive: tarfile.TarFile, digest: str) -> dict:
    algorithm, value = digest.split(":", 1)
    if algorithm != "sha256":
        raise ValueError("OCI digest is not SHA-256")
    return member_json(archive, f"blobs/sha256/{value}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))[0]
    expected_config = metadata["Id"]
    with tarfile.open(args.archive, "r") as archive:
        index = member_json(archive, "index.json")
        outer = blob_json(archive, index["manifests"][0]["digest"])
        candidates = [item for item in outer.get("manifests", [])
                      if item.get("platform") == {"architecture": "amd64", "os": "linux"}]
        if len(candidates) != 1:
            raise ValueError("OCI archive does not contain one linux/amd64 image")
        manifest_descriptor = candidates[0]
        manifest = blob_json(archive, manifest_descriptor["digest"])
        if manifest["config"]["digest"] != expected_config:
            raise ValueError("OCI config digest differs from tested Docker image ID")
        config = blob_json(archive, manifest["config"]["digest"])
        labels = config.get("config", {}).get("Labels", {})
        if labels.get("org.opencontainers.image.revision") != args.source_sha:
            raise ValueError("OCI source revision differs from exact head")

    document = {
        "schema": "codestra-oci-export-verification/v1",
        "source_sha": args.source_sha,
        "docker_image_id": expected_config,
        "oci_manifest_digest": manifest_descriptor["digest"],
        "archive_sha256": hashlib.sha256(args.archive.read_bytes()).hexdigest(),
        "status": "PASS",
    }
    args.output.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")
    print("OCI_EXPORT_VERIFICATION=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
