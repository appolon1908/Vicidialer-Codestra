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


def _direct_oci_archive(
    path,
    source_sha: str,
    architecture: str = "amd64",
    *,
    nested: bool = False,
) -> tuple[str, str]:
    layer_payload = b"deterministic layer contents\n"
    layer = gzip.compress(layer_payload, mtime=0)
    layer_descriptor = _descriptor(
        layer, "application/vnd.oci.image.layer.v1.tar+gzip"
    )
    diff_id = "sha256:" + hashlib.sha256(layer_payload).hexdigest()
    config = _json_bytes({
        "architecture": architecture,
        "os": "linux",
        "config": {"Labels": {"org.opencontainers.image.revision": source_sha}},
        "rootfs": {"type": "layers", "diff_ids": [diff_id]},
    })
    config_descriptor = _descriptor(config)
    manifest = _json_bytes({
        "schemaVersion": 2,
        "config": config_descriptor,
        "layers": [layer_descriptor],
    })
    manifest_descriptor = _descriptor(
        manifest,
        "application/vnd.oci.image.manifest.v1+json",
        platform={"architecture": architecture, "os": "linux"},
    )

    members = {
        "oci-layout": _json_bytes({"imageLayoutVersion": "1.0.0"}),
        f"blobs/sha256/{config_descriptor['digest'].split(':')[1]}": config,
        f"blobs/sha256/{manifest_descriptor['digest'].split(':')[1]}": manifest,
        f"blobs/sha256/{layer_descriptor['digest'].split(':')[1]}": layer,
    }
    if nested:
        outer = _json_bytes({
            "schemaVersion": 2,
            "manifests": [manifest_descriptor],
        })
        outer_descriptor = _descriptor(
            outer, "application/vnd.oci.image.index.v1+json"
        )
        members[f"blobs/sha256/{outer_descriptor['digest'].split(':')[1]}"] = outer
        index = {"schemaVersion": 2, "manifests": [outer_descriptor]}
    else:
        index = {"schemaVersion": 2, "manifests": [manifest_descriptor]}
    members["index.json"] = _json_bytes(index)
    _write_archive(path, members)
    return config_descriptor["digest"], diff_id


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


def _run_verifier(monkeypatch, tmp_path, architecture="amd64", *, nested=False):
    source_sha = "a" * 40
    archive = tmp_path / "image.oci.tar"
    image_id, diff_id = _direct_oci_archive(
        archive, source_sha, architecture, nested=nested
    )
    output = _configure_run(
        monkeypatch, tmp_path, archive, image_id, [diff_id], source_sha
    )
    return output


@pytest.mark.parametrize("nested", [False, True])
def test_verifier_accepts_direct_and_nested_linux_amd64_exports(
    monkeypatch, tmp_path, nested
):
    output = _run_verifier(monkeypatch, tmp_path, nested=nested)
    with pytest.raises(SystemExit, match="0"):
        runpy.run_path(SCRIPT, run_name="__main__")
    document = json.loads(output.read_text())
    assert document["status"] == "PASS"
    assert document["oci_layout_version"] == "1.0.0"
    assert document["oci_index_schema_version"] == 2


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
    image_id, diff_id = _direct_oci_archive(archive_path, source_sha)
    members = _read_members(archive_path)
    if mutation == "missing":
        del members["oci-layout"]
    elif mutation == "version":
        members["oci-layout"] = _json_bytes({"imageLayoutVersion": "0.9.0"})
    else:
        members["oci-layout"] = b"not json"
    _write_archive(archive_path, members)
    _configure_run(
        monkeypatch, tmp_path, archive_path, image_id, [diff_id], source_sha
    )
    with pytest.raises(ValueError, match=match):
        runpy.run_path(SCRIPT, run_name="__main__")


@pytest.mark.parametrize("value", [None, 1, "2", 2.0, True])
def test_verifier_requires_integer_oci_index_schema_version_two(
    monkeypatch, tmp_path, value
):
    source_sha = "a" * 40
    archive_path = tmp_path / "image.oci.tar"
    image_id, diff_id = _direct_oci_archive(archive_path, source_sha)
    members = _read_members(archive_path)
    index = json.loads(members["index.json"])
    if value is None:
        index.pop("schemaVersion")
    else:
        index["schemaVersion"] = value
    members["index.json"] = _json_bytes(index)
    _write_archive(archive_path, members)
    _configure_run(
        monkeypatch, tmp_path, archive_path, image_id, [diff_id], source_sha
    )
    with pytest.raises(ValueError, match="OCI index schemaVersion must be integer 2"):
        runpy.run_path(SCRIPT, run_name="__main__")


def test_verifier_requires_image_manifest_schema_version_two(
    monkeypatch, tmp_path
):
    source_sha = "a" * 40
    archive_path = tmp_path / "image.oci.tar"
    image_id, diff_id = _direct_oci_archive(archive_path, source_sha)
    members = _read_members(archive_path)
    index = json.loads(members["index.json"])
    old_name = "blobs/sha256/" + index["manifests"][0]["digest"].split(":")[1]
    manifest = json.loads(members.pop(old_name))
    manifest["schemaVersion"] = 1
    payload = _json_bytes(manifest)
    descriptor = _descriptor(
        payload,
        "application/vnd.oci.image.manifest.v1+json",
        platform={"architecture": "amd64", "os": "linux"},
    )
    index["manifests"][0] = descriptor
    members["index.json"] = _json_bytes(index)
    members["blobs/sha256/" + descriptor["digest"].split(":")[1]] = payload
    _write_archive(archive_path, members)
    _configure_run(
        monkeypatch, tmp_path, archive_path, image_id, [diff_id], source_sha
    )
    with pytest.raises(ValueError, match="OCI root schemaVersion must be integer 2"):
        runpy.run_path(SCRIPT, run_name="__main__")


@pytest.mark.parametrize("mutation,match", [
    ("blob", "blob digest mismatch"),
    ("size", "descriptor size mismatch"),
    ("missing", "member missing"),
    ("extra", "counts differ"),
    ("reordered", "diff-ID"),
    ("truncated", "descriptor size mismatch"),
    ("compression", "compression is invalid"),
])
def test_verifier_rejects_layer_identity_failures(
    monkeypatch, tmp_path, mutation, match
):
    source_sha = "a" * 40
    archive_path = tmp_path / "image.oci.tar"
    image_id, diff_id = _direct_oci_archive(archive_path, source_sha)
    members = _read_members(archive_path)
    index = json.loads(members["index.json"])
    manifest_name = (
        "blobs/sha256/" + index["manifests"][0]["digest"].split(":")[1]
    )
    manifest = json.loads(members[manifest_name])
    layer_name = (
        "blobs/sha256/" + manifest["layers"][0]["digest"].split(":")[1]
    )
    metadata_diff_ids = [diff_id]

    if mutation == "blob":
        members[layer_name] = members[layer_name][:-1] + b"x"
    elif mutation == "size":
        manifest["layers"][0]["size"] += 1
    elif mutation == "missing":
        del members[layer_name]
    elif mutation == "extra":
        manifest["layers"].append(manifest["layers"][0])
    elif mutation == "reordered":
        manifest["layers"].append(manifest["layers"][0])
        metadata_diff_ids = [diff_id, "sha256:" + "b" * 64]
    elif mutation == "truncated":
        members[layer_name] = members[layer_name][:-2]
    elif mutation == "compression":
        replacement = b"not gzip"
        descriptor = _descriptor(
            replacement, "application/vnd.oci.image.layer.v1.tar+gzip"
        )
        manifest["layers"][0] = descriptor
        del members[layer_name]
        layer_name = "blobs/sha256/" + descriptor["digest"].split(":")[1]
        members[layer_name] = replacement

    if mutation in {"size", "extra", "reordered", "compression"}:
        old_manifest_name = manifest_name
        payload = _json_bytes(manifest)
        descriptor = _descriptor(
            payload,
            "application/vnd.oci.image.manifest.v1+json",
            platform={"architecture": "amd64", "os": "linux"},
        )
        del members[old_manifest_name]
        index["manifests"][0] = descriptor
        manifest_name = "blobs/sha256/" + descriptor["digest"].split(":")[1]
        members[manifest_name] = payload
        members["index.json"] = _json_bytes(index)

    _write_archive(archive_path, members)
    _configure_run(
        monkeypatch,
        tmp_path,
        archive_path,
        image_id,
        metadata_diff_ids,
        source_sha,
    )
    with pytest.raises(ValueError, match=match):
        runpy.run_path(SCRIPT, run_name="__main__")


@pytest.mark.parametrize("target", ["config", "manifest"])
def test_verifier_authenticates_every_json_blob(monkeypatch, tmp_path, target):
    source_sha = "a" * 40
    archive_path = tmp_path / "image.oci.tar"
    image_id, diff_id = _direct_oci_archive(archive_path, source_sha)
    members = _read_members(archive_path)
    index = json.loads(members["index.json"])
    manifest_name = "blobs/sha256/" + index["manifests"][0]["digest"].split(":")[1]
    if target == "manifest":
        members[manifest_name] = members[manifest_name][:-1] + b" "
    else:
        manifest = json.loads(members[manifest_name])
        config_name = "blobs/sha256/" + manifest["config"]["digest"].split(":")[1]
        members[config_name] = members[config_name][:-1] + b" "
    _write_archive(archive_path, members)
    _configure_run(
        monkeypatch, tmp_path, archive_path, image_id, [diff_id], source_sha
    )
    with pytest.raises(ValueError, match="blob digest mismatch"):
        runpy.run_path(SCRIPT, run_name="__main__")
