"""Offline tests for the read-only internal call readiness probe."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vicidial/src"))
SPEC = importlib.util.spec_from_file_location(
    "internal_call_readiness", ROOT / "scripts/internal_call_readiness.py"
)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)

CLI_ROUTE = """[ Context 'codestra-internal-test' created by 'pbx_config' ]
  '6000' =>         1. Answer()                                   [extensions_custom.conf:12]
                    2. Echo()                                     [extensions_custom.conf:13]
                    3. Hangup()                                   [extensions_custom.conf:14]

-= 1 extension (3 priorities) in 1 context. =-
"""


class ReadinessProbeTests(unittest.TestCase):
    def setUp(self):
        probe.RESULTS.clear()

    def test_approved_route_digest_is_what_the_service_accepts(self):
        from codestra_vicidial.internal_call_transport import SocketAmiTransport

        rows = []
        for row in probe.APPROVED_ROUTE:
            message = types.SimpleNamespace(event="ListDialplan", get=row.get)
            rows.append(message)
        transport = SocketAmiTransport(Path("/unused"))
        transport._request = lambda *_args, **_kwargs: rows
        policy = types.SimpleNamespace(
            dialplan_context=probe.CONTEXT, route_sha256=probe.digest(probe.APPROVED_ROUTE)
        )
        transport.verify_route(policy)  # raises CallDenied on any mismatch
        self.assertEqual(
            probe.digest(probe.APPROVED_ROUTE),
            "f6dd114fa050a73fdbcf8fbfc72bdb8f08b9ddb462231b99cf81e4a7f3e5afa6",
        )

    def test_cli_dialplan_parses_to_approved_route(self):
        self.assertEqual(probe.parse_dialplan(CLI_ROUTE), probe.APPROVED_ROUTE)

    def test_dialplan_with_include_or_arguments_is_rejected(self):
        self.assertIsNone(probe.parse_dialplan(CLI_ROUTE + "  Include =>        'other'\n"))
        changed = probe.parse_dialplan(CLI_ROUTE.replace("Echo()", "Dial(PJSIP/100)"))
        self.assertNotEqual(changed, probe.APPROVED_ROUTE)

    def test_contacts_count_only_6901_rows(self):
        out = (
            "  Contact:  <Aor/ContactUri..............................> <Hash....> <Status> <RTT(ms)..>\n"
            "  Contact:  6901/sip:6901@10.0.0.5:5060;transport=ws   1a2b3c4d5e Avail         12.3\n"
        )
        self.assertEqual(probe.parse_contacts(out), ["Avail"])

    def test_env_values_are_parsed_without_quotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "a.env")
            path.write_text("# c\nexport A='x'\nB=\"y\"\nC=z\n")
            self.assertEqual(probe.read_env(path), {"A": "x", "B": "y", "C": "z"})

    def test_middleware_grant_checks_do_not_print_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            hmac = Path(tmp, "hmac")
            hmac.write_text("super-secret-value-that-must-not-print")
            os.chmod(hmac, 0o600)
            env = Path(tmp, "mw.env")
            env.write_text(
                "VICIDIAL_INTERNAL_CALL_BASE_URL=https://edge.example:8443/restricted-vicidial\n"
                "VICIDIAL_INTERNAL_CALL_EXPECTED_HOST=edge.example\n"
                f"VICIDIAL_INTERNAL_CALL_HMAC_FILE={hmac}\n"
            )
            grant = Path(tmp, "grant.json")
            grant.write_text(json.dumps({"principal": {"extension": "6902"}, "max_calls": 2}))
            from contextlib import redirect_stdout
            from io import StringIO

            buffer = StringIO()
            with redirect_stdout(buffer):
                probe.check_middleware(env, grant)
        output = buffer.getvalue()
        self.assertNotIn("super-secret", output)
        results = dict(probe.RESULTS)
        # A path-prefixed URL cannot work: the worker needs a bare origin.
        self.assertEqual(results["middleware_base_url_is_origin"], "FAIL")
        self.assertEqual(results["middleware_vicidial_internal_call_hmac_file"], "PASS")
        self.assertEqual(results["middleware_grant_literals"], "FAIL")


if __name__ == "__main__":
    unittest.main()
