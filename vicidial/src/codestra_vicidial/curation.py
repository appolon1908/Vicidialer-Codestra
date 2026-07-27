"""Publication-safety content detectors shared by inventory tooling and tests."""
from __future__ import annotations

import re

CONTENT_RULES = (
    ("private key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("authorization header", re.compile(
        rb"(?im)^authorization\s*:\s*(?:bearer|basic)\s+\S+"
    )),
    ("credential assignment", re.compile(
        rb"(?im)(?:password|passwd|client_secret|api_key|jwt_key|sip_secret)"
        rb"\s*[:=]\s*[^\s$<{][^\r\n]{3,}"
    )),
)


def content_categories(data: bytes) -> set[str]:
    return {category for category, rule in CONTENT_RULES if rule.search(data)}
