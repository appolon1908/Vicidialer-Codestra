"""Offline contract tests; none of these tests access Docker, hosts or providers."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "prepare_vicidial_environments", ROOT / "scripts/prepare_vicidial_environments.py"
)
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


class EnvironmentPreparationTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT / ".codestra/deployment-candidate.json").read_text())

    def test_observed_candidate_is_valid(self):
        self.assertIs(prepare.validate_manifest(self.manifest), self.manifest)

    def test_required_fields_cannot_be_omitted(self):
        fields = set(self.manifest) - {"evidence_origin"}
        for key in fields:
            with self.subTest(key=key):
                candidate = copy.deepcopy(self.manifest)
                del candidate[key]
                with self.assertRaises(ValueError):
                    prepare.validate_manifest(candidate)

    def test_mutable_or_foreign_artifacts_are_denied(self):
        for image in ("latest", "ghcr.io/other/adapter@" + self.manifest["artifact_digest"],
                      prepare.REGISTRY + ":main", prepare.REGISTRY + "@sha256:" + "0" * 64):
            with self.subTest(image=image):
                candidate = dict(self.manifest, artifact_reference=image)
                with self.assertRaises(ValueError):
                    prepare.validate_manifest(candidate)

    def test_invalid_git_identities_are_denied(self):
        for key in ("source_sha", "source_tree"):
            for value in ("main", "a" * 7, "A" * 40, "a" * 40 + "\n", None, 123, "../main"):
                with self.subTest(key=key, value=value):
                    with self.assertRaises(ValueError):
                        prepare.validate_manifest(dict(self.manifest, **{key: value}))

    def test_invalid_digests_are_denied(self):
        for value in ("", "sha256:abc", "sha512:" + "a" * 64, None, "sha256:" + "a" * 64 + "\n"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    prepare.validate_manifest(dict(self.manifest, artifact_digest=value))

    def test_unsafe_or_coerced_authorization_is_denied(self):
        for key in ("runtime_deployment_authorized", "external_effects_authorized",
                    "high_critical_vulnerabilities_allowed"):
            for value in (True, 0, "false", None):
                with self.subTest(key=key, value=value):
                    with self.assertRaises(ValueError):
                        prepare.validate_manifest(dict(self.manifest, **{key: value}))

    def test_verification_requirements_cannot_be_disabled(self):
        for key in ("signature_required", "sbom_required", "provenance_required"):
            for value in (False, 1, "true", None):
                with self.subTest(key=key, value=value):
                    with self.assertRaises(ValueError):
                        prepare.validate_manifest(dict(self.manifest, **{key: value}))

    def test_canary_is_strictly_bounded(self):
        for value in (-1, 0, 2, True, "1", None, float("nan"), float("inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    prepare.validate_manifest(dict(self.manifest, production_canary_max_percent=value))

    def test_mutating_methods_and_other_probe_paths_are_denied(self):
        for key, value in (("production_allowed_methods", ["GET", "POST"]),
                           ("health_paths", ["/health", "/ready", "/v1/calls/originate"])):
            with self.assertRaises(ValueError):
                prepare.validate_manifest(dict(self.manifest, **{key: value}))

    def test_staging_has_no_network_ports_or_database_mount(self):
        docs = prepare.preparation_documents(self.manifest)
        compose = json.loads(docs["staging.compose.json"])
        service = compose["services"]["restricted-adapter-staging"]
        self.assertEqual(service["network_mode"], "none")
        self.assertNotIn("ports", service)
        self.assertNotIn("networks", compose)
        self.assertNotIn("privileged", service)
        self.assertEqual(service["volumes"], ["staging-state:/var/lib/codestra-vicidial"])
        self.assertEqual(service["image"], self.manifest["artifact_reference"])
        self.assertEqual(service["pull_policy"], "never")
        self.assertEqual(service["environment"]["CODESTRA_ADAPTER_BACKEND"], "memory")

    def test_staging_profile_matches_memory_backend_contract(self):
        docs = prepare.preparation_documents(self.manifest)
        service = json.loads(docs["staging.compose.json"])["services"]["restricted-adapter-staging"]
        self.assertEqual(service["environment"]["CODESTRA_ADAPTER_DEPLOYMENT"], "staging-read-only")
        self.assertEqual(service["environment"]["CODESTRA_ADAPTER_BACKEND"], "memory")

    def test_production_reference_retains_its_mysql_candidate_profile(self):
        docs = prepare.preparation_documents(self.manifest)
        values = dict(line.split("=", 1) for line in docs["production.env.example"].splitlines()
                      if line and not line.startswith("#"))
        self.assertEqual(values["CODESTRA_ADAPTER_DEPLOYMENT"], "production-candidate")
        self.assertEqual(values["CODESTRA_ADAPTER_BACKEND"], "mysql")
        self.assertNotIn("staging-read-only", docs["production.env.example"])

    def test_staging_is_nonroot_readonly_and_bounded(self):
        service = json.loads(prepare.preparation_documents(self.manifest)["staging.compose.json"])["services"]["restricted-adapter-staging"]
        self.assertEqual(service["user"], "10001:10001")
        self.assertIs(service["read_only"], True)
        self.assertEqual(service["cap_drop"], ["ALL"])
        self.assertEqual(service["security_opt"], ["no-new-privileges:true"])
        self.assertEqual(service["restart"], "no")
        self.assertEqual(service["pids_limit"], 128)

    def test_all_capabilities_remain_false_in_both_environments(self):
        docs = prepare.preparation_documents(self.manifest)
        service = json.loads(docs["staging.compose.json"])["services"]["restricted-adapter-staging"]
        for flag in prepare.FLAGS:
            self.assertEqual(service["environment"][flag], "false")
            self.assertIn(flag + "=false\n", docs["production.env.example"])

    def test_staging_and_production_do_not_share_secret_references(self):
        docs = prepare.preparation_documents(self.manifest)
        self.assertIn("VICIDIAL_STAGING_HMAC_FILE", docs["staging.compose.json"])
        self.assertNotIn("CODESTRA_ADAPTER_HMAC_SECRET_FILE", docs["staging.compose.json"])
        self.assertIn("CODESTRA_ADAPTER_HMAC_SECRET_FILE=\n", docs["production.env.example"])
        self.assertIn("CODESTRA_VICIDIAL_DB_SECRET_FILE=\n", docs["production.env.example"])

    def test_preparation_is_not_runtime_certification_or_authorization(self):
        plan = json.loads(prepare.preparation_documents(self.manifest)["deployment-plan.json"])
        self.assertIs(plan["runtime_deployment_authorized"], False)
        self.assertIs(plan["production_ready"], False)
        self.assertEqual(plan["runtime_certification"], "NOT_RUN")
        self.assertEqual(set(plan["required_gates"].values()), {"NOT_VERIFIED"})
        self.assertIsNone(plan["staging_host"])
        self.assertEqual(plan["production_private_ingress"], "10.40.0.2:8443")
        self.assertEqual(plan["production_runtime_authority"], "existing-reviewed-server-b-operator")
        self.assertIsNone(plan["production_source_archive_requirement"]["archive_sha256"])

    def test_output_is_deterministic_and_input_is_not_mutated(self):
        original = copy.deepcopy(self.manifest)
        self.assertEqual(prepare.preparation_documents(self.manifest), prepare.preparation_documents(self.manifest))
        self.assertEqual(self.manifest, original)

    def test_writes_checksummed_package_without_secret_material(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "prepared"
            prepare.write_preparation(self.manifest, target)
            self.assertEqual({p.name for p in target.iterdir()}, {
                "staging.compose.json", "production.env.example", "deployment-plan.json", "SHA256SUMS"
            })
            for line in (target / "SHA256SUMS").read_text().splitlines():
                checksum, name = line.split("  ")
                self.assertEqual(hashlib.sha256((target / name).read_bytes()).hexdigest(), checksum)

    def test_existing_package_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            sentinel = target / "operator-file"
            sentinel.write_text("preserve")
            with self.assertRaises(FileExistsError):
                prepare.write_preparation(self.manifest, target)
            self.assertEqual(sentinel.read_text(), "preserve")
            self.assertEqual(list(target.iterdir()), [sentinel])

    def test_invalid_manifest_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "must-not-exist"
            with self.assertRaises(ValueError):
                prepare.write_preparation({}, target)
            self.assertFalse(target.exists())

    def test_cli_rejects_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "duplicate.json"
            manifest.write_text('{"runtime_deployment_authorized":true,"runtime_deployment_authorized":false}')
            target = Path(directory) / "must-not-exist"
            with mock.patch("sys.stderr"):
                self.assertEqual(prepare.main(["--manifest", str(manifest), "--output", str(target)]), 2)
            self.assertFalse(target.exists())

    def test_cli_failures_do_not_echo_sensitive_content(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "input.json"
            manifest.write_text('{"unexpected":"synthetic-private-content"}')
            with mock.patch("sys.stderr") as stderr:
                self.assertEqual(prepare.main(["--manifest", str(manifest), "--output", str(Path(directory) / "out")]), 2)
                self.assertNotIn("synthetic-private-content", str(stderr.mock_calls))


class PreparationWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.release = (ROOT / ".github/workflows/publish-server-b-source-release.yml").read_text()
        self.workflow = (ROOT / ".github/workflows/vicidial-environment-preparation.yml").read_text()

    def test_publication_uses_explicit_source_not_old_fixed_pin(self):
        self.assertIn("SOURCE_SHA: ${{ inputs.source_sha }}", self.release)
        self.assertNotIn("SOURCE_SHA: 9ac8ef4840f78ba4ad9b816e4e409298505103ce", self.release)
        self.assertIn("test \"$PUBLICATION_CONFIRMATION\" = PUBLISH_SERVER_B_SOURCE_RELEASE", self.release)

    def test_source_ancestry_is_bound_to_immutable_builder_before_checkout(self):
        guard = self.release.index('git merge-base --is-ancestor "$SOURCE_SHA" "$GITHUB_SHA"')
        source_checkout = self.release.index("name: Check out exact protected adapter source")
        self.assertLess(guard, source_checkout)
        self.assertIn('[[ "$SOURCE_SHA" =~ ^[0-9a-f]{40}$ ]]', self.release)
        self.assertIn("if: github.ref == 'refs/heads/main'", self.release)
        self.assertEqual(self.release.count("persist-credentials: false"), 2)

    def test_publication_retains_full_source_tests_and_signed_bindings(self):
        self.assertIn("cd exact-source && python -m pytest -q vicidial/tests tests", self.release)
        self.assertIn("test_source_release_artifact.py", self.release)
        self.assertIn("verify_source_release.py", self.release)
        self.assertEqual(self.release.count('"$cosign" sign-blob'), 2)
        self.assertEqual(self.release.count('"$cosign" verify-blob'), 2)
        self.assertIn("--builder-sha \"$GITHUB_SHA\"", self.release)

    def test_preparation_has_readonly_permission_and_no_deployment_commands(self):
        self.assertIn("contents: read", self.workflow)
        for forbidden in ("id-token: write", "packages: write", "environment:", "secrets.",
                          "docker compose up", "ssh ", "sudo ", "systemctl "):
            self.assertNotIn(forbidden, self.workflow)
        self.assertIn("config --no-env-resolution --quiet", self.workflow)
        self.assertIn("sha256sum --check SHA256SUMS", self.workflow)

    def test_preparation_distinguishes_builder_sha_from_runtime_candidate(self):
        self.assertIn("PREPARATION_SHA:", self.workflow)
        self.assertIn("--manifest .codestra/deployment-candidate.json", self.workflow)
        self.assertIn("not a certification or release", self.workflow)


if __name__ == "__main__":
    unittest.main()
