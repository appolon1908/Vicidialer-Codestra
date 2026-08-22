from pathlib import Path


OPS = Path(__file__).parents[1] / "ops"
GRANTS = (OPS / "reconciliation-db-grants.sql.template").read_text(encoding="utf-8")
REVOKE = (OPS / "reconciliation-db-grants-revoke.sql.template").read_text(
    encoding="utf-8"
)

TABLES = {
    "vicidial_campaigns",
    "vicidial_lists",
    "vicidial_user_groups",
    "vicidial_inbound_groups",
    "vicidial_scripts",
    "vicidial_campaign_statuses",
}


def test_grants_are_local_table_and_column_scoped() -> None:
    assert "'codestra-adapter'@'localhost'" in GRANTS
    assert "ON *.*" not in GRANTS
    assert "ON asterisk.*" not in GRANTS
    assert "DELETE" not in GRANTS
    assert "CREATE" not in GRANTS
    assert "DROP" not in GRANTS
    assert "ALTER" not in GRANTS
    for table in TABLES:
        assert f"ON asterisk.{table}" in GRANTS


def test_grants_cover_only_repository_campaign_writes() -> None:
    repository = (
        Path(__file__).parents[1] / "src/codestra_vicidial/repository.py"
    ).read_text(encoding="utf-8")
    for table in TABLES:
        assert table in repository
    assert "vicidial_users" not in GRANTS
    assert "vicidial_callbacks" not in GRANTS
    assert "vicidial_list" not in GRANTS.replace("vicidial_lists", "")


def test_every_grant_has_an_emergency_revoke() -> None:
    for table in TABLES:
        assert f"ON asterisk.{table}" in REVOKE
    assert "DELETE" not in REVOKE
    assert "DROP" not in REVOKE
