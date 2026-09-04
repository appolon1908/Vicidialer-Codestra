from __future__ import annotations

import gzip
import hashlib
import importlib.util
import inspect
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "vicidial/scripts/verify_oci_export.py"
SPEC = importlib.util.spec_from_file_location("verify_oci_export_media_types", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


def test_image_config_media_type_is_required_before_config_is_decoded():
    with pytest.raises(
        ValueError,
        match="OCI image config descriptor media type is invalid",
    ):
        VERIFIER.require_media_type(
            {"mediaType": "application/json"},
            VERIFIER.OCI_IMAGE_CONFIG,
            "OCI image config descriptor",
        )

    source = inspect.getsource(VERIFIER.main)
    descriptor = source.index('config_descriptor = manifest.get("config")')
    media_type_check = source.index("require_media_type(", descriptor)
    decode = source.index("config = descriptor_json(", descriptor)
    assert descriptor < media_type_check < decode


def test_supported_oci_and_docker_layer_media_types_have_exact_semantics():
    payload = b"deterministic rootfs layer\n"
    compressed = gzip.compress(payload, mtime=0)
    expected = "sha256:" + hashlib.sha256(payload).hexdigest()

    for media_type in (
        VERIFIER.OCI_LAYER_GZIP,
        VERIFIER.DOCKER_LAYER_GZIP,
    ):
        assert VERIFIER.layer_diff_id(compressed, media_type) == expected

    for media_type in (
        VERIFIER.OCI_LAYER_TAR,
        VERIFIER.DOCKER_LAYER_TAR,
    ):
        assert VERIFIER.layer_diff_id(payload, media_type) == expected


@pytest.mark.parametrize(
    "media_type",
    [
        "application/example+gzip",
        "application/vnd.oci.image.layer.v1.tar+zstd",
        "application/json",
        "",
    ],
)
def test_unsupported_layer_media_types_fail_closed(media_type: str):
    with pytest.raises(ValueError, match="unsupported OCI layer media type"):
        VERIFIER.layer_diff_id(b"payload", media_type)
