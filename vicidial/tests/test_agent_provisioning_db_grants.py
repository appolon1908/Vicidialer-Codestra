from pathlib import Path


OPS = Path(__file__).parents[1] / "ops"
GRANT = (OPS / "agent-provisioning-db-grants.sql.template").read_text()
REVOKE = (OPS / "agent-provisioning-db-grants-revoke.sql.template").read_text()


def test_agent_grant_is_local_column_scoped_and_reversible():
    assert "ON asterisk.vicidial_users" in GRANT
    assert "'codestra-adapter'@'localhost'" in GRANT
    assert "INSERT (user, pass, full_name, user_level, user_group, active, closer_campaigns)" in GRANT
    assert "UPDATE (active)" in GRANT
    for forbidden in ("ON *.*", "DELETE", "DROP", "ALTER", "CREATE"):
        assert forbidden not in GRANT
    assert "REVOKE INSERT, UPDATE ON asterisk.vicidial_users" in REVOKE


def test_agent_grant_is_separate_from_campaign_reconciliation():
    campaign = (OPS / "reconciliation-db-grants.sql.template").read_text()
    assert "vicidial_users" not in campaign
