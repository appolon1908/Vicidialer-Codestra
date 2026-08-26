from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "operations/odoo-n8n/production-preflight.sh"


def test_preflight_is_read_only_and_fail_closed() -> None:
    text = SCRIPT.read_text()
    for prohibited in (
        "docker restart",
        "docker pause",
        "docker unpause",
        "docker compose up",
        "docker rm",
        "systemctl restart",
        "systemctl reload",
        "pg_dump",
        "CREATE ",
        "ALTER ",
        "DROP ",
    ):
        assert prohibited not in text
    assert "SHARED_POSTGRES_CUTOVER_GO=YES" in text
    assert "ODOO_N8N_READY_FOR_CHANGE_WINDOW=NO" in text


def test_preflight_covers_both_apps_and_private_boundaries() -> None:
    text = SCRIPT.read_text()
    for required in (
        "codestra-odoo-1",
        "codestra-n8n-1",
        "codestra_odoo",
        "codestra_n8n",
        "odoo_app",
        "n8n_app",
        "rolcreatedb",
        "rolcreaterole",
        "rolreplication",
        "rolbypassrls",
        "has_database_privilege",
        "has_table_privilege",
        "pg_auth_members",
        "odoo.internal.codestra.agency:443",
        "n8n.internal.codestra.agency:443",
        "codestra-postgres-1:5432",
        ".HostConfig.PortBindings",
        "ir_module_module",
        "archive_mode",
    ):
        assert required in text
