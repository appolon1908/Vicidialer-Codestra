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


class InternalCallIngressTests(unittest.TestCase):
    INCLUDE = ROOT / "vicidial/ops/apache/codestra-vicidial-restricted-adapter.include"
    OPERATION = "0b7e5c1e-6f0a-5d8e-9a53-2f4a6c1d9e70"

    def blocks(self):
        import re

        text = self.INCLUDE.read_text()
        return [
            (re.compile(pattern), body)
            for pattern, body in re.findall(
                r'<LocationMatch "(\^/v1/calls/internal/[^"]*)">(.*?)</LocationMatch>', text, re.S
            )
        ]

    def matching(self, path):
        return [body for pattern, body in self.blocks() if pattern.search(path)]

    def test_middleware_origin_routes_are_guarded_and_proxied(self):
        text = self.INCLUDE.read_text()
        self.assertIn(
            'ProxyPass "/v1/calls/internal/" "http://127.0.0.1:8097/v1/calls/internal/"', text
        )
        for path, method in (
            ("/v1/calls/internal/originate", "POST"),
            (f"/v1/calls/internal/{self.OPERATION}/hangup", "POST"),
            (f"/v1/calls/internal/{self.OPERATION}", "GET"),
        ):
            bodies = self.matching(path)
            self.assertEqual(len(bodies), 1, path)
            self.assertIn(f"Require method {method}", bodies[0])
            self.assertIn("Require ip 10.40.0.1", bodies[0])
            self.assertIn("%{SSL_CLIENT_VERIFY} == 'SUCCESS'", bodies[0])

    def test_other_internal_paths_are_denied(self):
        for path in (
            "/v1/calls/internal/",
            "/v1/calls/internal/short",
            f"/v1/calls/internal/{self.OPERATION}/replay",
            "/v1/calls/internal/originate/extra",
            "/v1/calls/internal/../originate",
        ):
            bodies = self.matching(path)
            self.assertEqual(len(bodies), 1, path)
            self.assertIn("Require all denied", bodies[0])

    def test_probe_accepts_the_shipped_include(self):
        self.assertEqual(probe.ingress_problems(self.INCLUDE.read_text()), [])

    def test_probe_ignores_commented_or_unguarded_routes(self):
        text = self.INCLUDE.read_text()
        commented = "\n".join("# " + line for line in text.splitlines())
        self.assertIn("proxy_missing", probe.ingress_problems(commented))
        head, marker, tail = text.partition('<LocationMatch "^/v1/calls/internal/')
        unguarded = head + marker + tail.replace("        Require ip 10.40.0.1\n", "", 1)
        self.assertTrue(probe.ingress_problems(unguarded))
        self.assertTrue(probe.ingress_problems(text.replace("(?!originate$)", "")))

    def test_only_includes_inside_the_vhost_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "conf.d").mkdir()
            (root / "conf.d/ingress.include").write_text(self.INCLUDE.read_text())
            (root / "conf.d/ingress.include.bak").write_text(self.INCLUDE.read_text())
            active = root / "vhost.conf"
            active.write_text(
                "Listen 10.40.0.2:8443\n"
                "<VirtualHost 10.40.0.2:8443>\n"
                "    SSLVerifyClient require\n"
                "    Include conf.d/ingress.include\n"
                "</VirtualHost>\n"
            )
            self.assertEqual(probe.ingress_problems(probe.vhost_block(active, 2, root)), [])
            # A backup file next to it is not part of the vhost.
            active.write_text(
                "<VirtualHost 10.40.0.2:8443>\n"
                "    # Include conf.d/ingress.include\n"
                "</VirtualHost>\n"
            )
            self.assertIn("proxy_missing", probe.ingress_problems(probe.vhost_block(active, 1, root)))


class PhaseTests(unittest.TestCase):
    STEADY = {"CODESTRA_ADAPTER_DEPLOYMENT": "production-candidate", "ENABLE_INTERNAL_TEST_CALLING": "false"}
    WINDOW = {
        "CODESTRA_ADAPTER_DEPLOYMENT": "production-internal-test",
        "ENABLE_INTERNAL_TEST_CALLING": "true",
        "CODESTRA_ADAPTER_BACKEND": "mysql",
        "CODESTRA_RELEASE_METADATA_REQUIRED": "true",
        "CODESTRA_INTERNAL_CALL_STATE": "/var/lib/codestra-vicidial-restricted-adapter/internal-calls.sqlite3",
        "CODESTRA_ADAPTER_STATE": "/var/lib/codestra-vicidial-restricted-adapter/state.sqlite3",
    }

    def setUp(self):
        probe.RESULTS.clear()

    def run_quiet(self, func, *args):
        from contextlib import redirect_stdout
        from io import StringIO

        with redirect_stdout(StringIO()):
            func(*args)
        return dict(probe.RESULTS)

    def test_window_env_must_couple_profile_and_gate(self):
        self.assertNotIn("FAIL", self.run_quiet(probe.check_window_env, self.WINDOW, "staged").values())
        probe.RESULTS.clear()
        gate_off = {**self.WINDOW, "ENABLE_INTERNAL_TEST_CALLING": "false"}
        results = self.run_quiet(probe.check_window_env, gate_off, "staged")
        self.assertEqual(results["staged_internal_call_gate"], "FAIL")

    def test_identity_binding_is_separate_from_activation(self):
        steady = ["N", "8", "WEBTEST_SYN", " TEST_SYN -", "N:6901", "N"]
        problems, activation = probe.identity_findings(steady)
        self.assertEqual(problems, [])
        self.assertEqual(activation, {"user": "N", "phone": "N", "campaign": "N"})
        problems, _ = probe.identity_findings(["Y", "9", "OTHER", " TEST_SYN OTHER -", "", "Y"])
        self.assertEqual(len(problems), 4)

    def test_steady_copy_must_match_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            live, steady = Path(tmp, "live.env"), Path(tmp, "steady.env")
            body = "".join(f"{k}={v}\n" for k, v in self.STEADY.items())
            live.write_text(body)
            steady.write_text(body)
            os.chmod(steady, 0o600)
            results = self.run_quiet(probe.check_steady_copy, live, steady, "closed")
            # Not root-owned in the test sandbox, so protection fails but content matches.
            self.assertEqual(results["steady_env_copy"], "PASS" if os.geteuid() == 0 else "FAIL")
            probe.RESULTS.clear()
            live.write_text(body.replace("false", "true"))
            results = self.run_quiet(probe.check_steady_copy, live, steady, "closed")
            self.assertEqual(results["steady_env_copy"], "FAIL")

    def test_preflight_requires_the_staged_window_env(self):
        argv = sys.argv
        sys.argv = ["internal_call_readiness.py", "--phase", "preflight"]
        try:
            with self.assertRaises(SystemExit) as raised:
                from contextlib import redirect_stderr
                from io import StringIO

                with redirect_stderr(StringIO()):
                    probe.main()
            self.assertEqual(raised.exception.code, 2)
        finally:
            sys.argv = argv


if __name__ == "__main__":
    unittest.main()
