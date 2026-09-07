#!/usr/bin/env python3
"""Bounded runtime regressions for security fixes in the pinned Python image."""

from __future__ import annotations

import http.cookies
import json
import multiprocessing
import pickle
import pyexpat
import resource
import ssl
import subprocess
import sys


def _parse_nested_model(depth: int) -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
    resource.setrlimit(resource.RLIMIT_AS, (128 * 1024 * 1024,) * 2)
    parser = pyexpat.ParserCreate()
    parser.ElementDeclHandler = lambda *_: None
    try:
        parser.Parse(f"<!DOCTYPE x [<!ELEMENT x {'(' * depth}x{')' * depth}>]><x/>", True)
    except (RecursionError, pyexpat.ExpatError):
        # A bounded Python exception is an acceptable safe rejection. The
        # vulnerable behavior is an unbounded C-stack overflow or crash.
        return


def _expect_cookie_error(operation) -> None:
    try:
        operation()
    except http.cookies.CookieError:
        return
    raise AssertionError("control character was accepted")


def main() -> None:
    assert sys.version_info[:3] == (3, 11, 16)
    assert sys.implementation.name == "cpython"
    assert tuple(map(int, pyexpat.EXPAT_VERSION.removeprefix("expat_").split("."))) >= (2, 8, 0)

    process = multiprocessing.Process(target=_parse_nested_model, args=(2_000,))
    process.start()
    process.join(5)
    if process.is_alive():
        process.kill()
        process.join()
        raise AssertionError("nested Expat model exceeded timeout")
    assert process.exitcode == 0, f"nested Expat model exited {process.exitcode}"

    morsel = http.cookies.Morsel()
    morsel.set("name", "value", "value")
    _expect_cookie_error(lambda: morsel.update({"path": "bad\r\nInjected: true"}))
    _expect_cookie_error(lambda: morsel.__ior__({"path": "bad\x00value"}))
    restored = pickle.loads(pickle.dumps(morsel))
    _expect_cookie_error(lambda: restored.update({"comment": "bad\x01value"}))
    dict.__setitem__(restored, "path", "bad\r\nInjected: true")
    _expect_cookie_error(restored.js_output)

    packages = {}
    for package in ("libcrypto3", "libssl3", "libuuid"):
        output = subprocess.check_output(["apk", "list", "--installed", package],
                                         text=True, stderr=subprocess.DEVNULL)
        packages[package] = output.split()[0].removeprefix(package + "-")
    print(json.dumps({
        "python": sys.version,
        "python_short": ".".join(map(str, sys.version_info[:3])),
        "implementation": sys.implementation.name,
        "expat": pyexpat.EXPAT_VERSION,
        "expat_short": pyexpat.EXPAT_VERSION.removeprefix("expat_"),
        "openssl": ssl.OPENSSL_VERSION,
        "openssl_short": ssl.OPENSSL_VERSION.split()[1],
        **packages,
        "cve_2026_4224": "PASS",
        "cve_2026_3644": "PASS",
        "cve_2026_7210_runtime_component": "PASS",
    }, sort_keys=True))


if __name__ == "__main__":
    main()
