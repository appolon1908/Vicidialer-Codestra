from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "vicidial" / "src" / "codestra_vicidial" / "app.py"
PRODUCTION_COMPOSE = ROOT / "vicidial" / "docker" / "compose.production.yml"
ADAPTER_ENV = ROOT / "vicidial" / "config" / "adapter.env.example"


CANONICAL_MUTATION_FLAGS = {
    "ENABLE_VICIDIAL_WRITES",
    "ENABLE_PROVISIONING",
    "ENABLE_AGENT_SYNC",
    "ENABLE_LEAD_PUBLICATION",
    "ENABLE_WARM_TRANSFER",
    "ENABLE_IVR_CALLBACKS",
    "ENABLE_PRODUCTION_DIALING",
}

DEPRECATED_FLAG_NAMES = {
    "ENABLE_CALLBACK_WRITES",
    "ENABLE_TRANSFER_POLICY_SYNC",
    "ENABLE_PSTN_DIALING",
}


def test_production_compose_uses_application_feature_flag_names():
    app = APP.read_text()
    compose = PRODUCTION_COMPOSE.read_text()
    example = ADAPTER_ENV.read_text()

    # The deployment contract must use names that the running application
    # actually consumes. A renamed-but-unread flag is a dangerous false switch.
    for name in CANONICAL_MUTATION_FLAGS:
        assert name in compose or name == "ENABLE_PRODUCTION_DIALING"
        assert name in example

    assert 'os.environ.get("ENABLE_WARM_TRANSFER")' in app
    assert 'os.environ.get("ENABLE_IVR_CALLBACKS")' in app

    for name in DEPRECATED_FLAG_NAMES:
        assert name not in compose


def test_production_compose_keeps_all_live_capabilities_fail_closed():
    compose = PRODUCTION_COMPOSE.read_text()
    required_false = {
        "ENABLE_VICIDIAL_WRITES",
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
    for name in required_false:
        assert f'{name}: "false"' in compose


def test_canonical_sync_modules_exist_as_executable_runtime_sources():
    canonical_sync = ROOT / "scripts" / "vicidial_sync.py"
    canonical_events = ROOT / "scripts" / "canonical_event_client.py"
    assert canonical_sync.is_file()
    assert canonical_events.is_file()

    sync_source = canonical_sync.read_text()
    assert "from canonical_event_client import" in sync_source
    assert ".reference" not in sync_source
    assert ".candidate" not in sync_source


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
