from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "vicidial" / "src" / "codestra_vicidial" / "app.py"
PRODUCTION_COMPOSE = ROOT / "vicidial" / "docker" / "compose.production.yml"
SHADOW_COMPOSE = ROOT / "vicidial" / "docker" / "compose.shadow.yml"
ENTRYPOINT = ROOT / "vicidial" / "docker" / "entrypoint.sh"
DEPLOY_READINESS = ROOT / ".github" / "workflows" / "codestra-deploy-readiness.yml"
ADAPTER_ENV = ROOT / "vicidial" / "config" / "adapter.env.example"
SYSTEMD_UNITS = (
    ROOT / "vicidial" / "ops" / "codestra-vicidial-restricted-adapter.service",
    ROOT / "deploy" / "vicidial-restricted-adapter" / "codestra-vicidial-restricted-adapter.service",
)


CANONICAL_MUTATION_FLAGS = {
    "ENABLE_VICIDIAL_WRITES",
    "ENABLE_AGENT_PROVISIONING",
    "ENABLE_PROVISIONING",
    "ENABLE_AGENT_SYNC",
    "ENABLE_LEAD_PUBLICATION",
    "ENABLE_WARM_TRANSFER",
    "ENABLE_IVR_CALLBACKS",
    "ENABLE_PRODUCTION_DIALING",
}

ALL_FAIL_CLOSED_FLAGS = {
    "ENABLE_VICIDIAL_WRITES",
    "ENABLE_AGENT_PROVISIONING",
    "ENABLE_PROVISIONING",
    "ENABLE_AGENT_SYNC",
    "ENABLE_LEAD_PUBLICATION",
    "ENABLE_LIVE_CALL_CONTROL",
    "ENABLE_WARM_TRANSFER",
    "ENABLE_IVR_ROUTING",
    "ENABLE_IVR_CALLBACKS",
    "ENABLE_PRODUCTION_DIALING",
    "ENABLE_SMS_DELIVERY",
    "ENABLE_EMAIL_DELIVERY",
    "ENABLE_EXTERNAL_DELIVERY",
    "ENABLE_AI_ACTIONS",
}

DEPRECATED_FLAG_NAMES = {
    "ENABLE_CALLBACK_WRITES",
    "ENABLE_TRANSFER_POLICY_SYNC",
    "ENABLE_PSTN_DIALING",
}


def test_compose_uses_application_feature_flag_names():
    app = APP.read_text()
    example = ADAPTER_ENV.read_text()
    for compose_path in (PRODUCTION_COMPOSE, SHADOW_COMPOSE):
        compose = compose_path.read_text()
        for name in CANONICAL_MUTATION_FLAGS:
            assert name in compose
        for name in DEPRECATED_FLAG_NAMES:
            assert name not in compose

    for name in CANONICAL_MUTATION_FLAGS:
        assert name in example

    assert 'os.environ.get("ENABLE_WARM_TRANSFER")' in app
    assert 'os.environ.get("ENABLE_IVR_CALLBACKS")' in app


def test_production_and_shadow_compose_keep_live_capabilities_fail_closed():
    for compose_path in (PRODUCTION_COMPOSE, SHADOW_COMPOSE):
        compose = compose_path.read_text()
        for name in ALL_FAIL_CLOSED_FLAGS:
            assert f'{name}: "false"' in compose


def test_container_entrypoint_enforces_canonical_kill_switches_and_rejects_aliases():
    entrypoint = ENTRYPOINT.read_text()
    for name in ALL_FAIL_CLOSED_FLAGS:
        assert name in entrypoint
    for name in DEPRECATED_FLAG_NAMES:
        assert name in entrypoint
    assert "deprecated feature flag" in entrypoint


def test_canonical_sync_modules_exist_as_executable_runtime_sources():
    canonical_sync = ROOT / "scripts" / "vicidial_sync.py"
    canonical_events = ROOT / "scripts" / "canonical_event_client.py"
    assert canonical_sync.is_file()
    assert canonical_events.is_file()

    sync_source = canonical_sync.read_text()
    assert "from canonical_event_client import" in sync_source
    assert ".reference" not in sync_source
    assert ".candidate" not in sync_source


def test_legacy_sync_fixtures_are_byte_identical_to_canonical_until_removed():
    # The historical root integration tests still load the retained migration
    # filenames. Byte-parity prevents those tests from certifying different
    # code than production while the historical fixtures are being retired.
    assert (ROOT / "scripts" / "vicidial_sync.py").read_bytes() == (
        ROOT / "scripts" / "vicidial_sync.py.candidate"
    ).read_bytes()
    assert (ROOT / "scripts" / "canonical_event_client.py").read_bytes() == (
        ROOT / "scripts" / "canonical_event_client.py.reference"
    ).read_bytes()


def test_all_deployment_authority_uses_dependency_aware_production_app():
    dockerfile = (ROOT / "vicidial" / "docker" / "Dockerfile").read_text()
    assert "codestra_vicidial.production_app:app" in dockerfile
    assert "codestra_vicidial.app:app" not in dockerfile
    for unit in SYSTEMD_UNITS:
        text = unit.read_text()
        assert "codestra_vicidial.production_app:app" in text
        assert "codestra_vicidial.app:app" not in text


def test_deployment_authority_does_not_execute_reference_or_candidate_files():
    roots = [
        ROOT / ".github" / "workflows",
        ROOT / "deploy",
        ROOT / "vicidial" / "docker",
        ROOT / "vicidial" / "ops",
    ]
    forbidden = ("vicidial_sync.py.candidate", "canonical_event_client.py.reference")
    for directory in roots:
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            text = path.read_text(errors="ignore")
            for name in forbidden:
                assert name not in text, f"{path} executes legacy {name}"


def test_deploy_readiness_uses_repository_root_container_contract():
    workflow = DEPLOY_READINESS.read_text()
    assert "@7db424a47c5556dd473268fa484665c0bb5ee215" in workflow
    assert "dockerfile_path: vicidial/docker/Dockerfile" in workflow
    assert "docker_build_context: ." in workflow
