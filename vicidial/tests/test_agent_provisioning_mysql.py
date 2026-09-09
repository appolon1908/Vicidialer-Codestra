"""Isolated real-MariaDB rollback evidence for bounded agent provisioning."""
import os

import pytest

from codestra_vicidial.models import ProvisionAgentCommand
from codestra_vicidial.reconciliation import canonical_sha256
from codestra_vicidial.repository import CollisionError, MySQLRepository

pytestmark = pytest.mark.skipif(not os.getenv("VICIDIAL_TEST_MYSQL_PORT"), reason="isolated MariaDB not configured")


def command():
    agent = {"user_id": "appolon", "full_name": "Appolon Internal Supervisor",
             "user_group": "WEBTEST_SYN", "campaigns": ["TEST_SYN"],
             "inbound_groups": [], "active": False, "adopt_existing_sha256": None,
             "extension": "6901", "role": "supervisor", "campaign": "TEST_SYN",
             "internal_only": True, "external_dialing": False}
    return ProvisionAgentCommand.model_validate({
        "context": {"correlation_id": "mysql-rollback-correlation", "actor": "test",
                    "tenant_id": "tenant-test", "business_unit": "TEST",
                    "campaign_id": "TEST_SYN", "supervisor_subject": "subject-appolon",
                    "expected_version": 0,
                    "reason": "isolated rollback test", "requested_at": "2026-09-05T12:00:00Z"},
        "agent": agent, "user_level": 8, "authorization_reference": "CHG-20260905-MYSQL-TEST",
        "plan_sha256": canonical_sha256(agent), "backup_reference": "isolated-backup-20260905",
    })


@pytest.fixture
def repository():
    import pymysql
    connection = pymysql.connect(host="127.0.0.1", port=int(os.environ["VICIDIAL_TEST_MYSQL_PORT"]),
        user="root", password=os.environ["VICIDIAL_TEST_MYSQL_PASSWORD"], database="asterisk",
        autocommit=False, cursorclass=pymysql.cursors.DictCursor)
    with connection.cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS phones")
        cursor.execute("DROP TABLE IF EXISTS vicidial_users")
        cursor.execute("DROP TABLE IF EXISTS vicidial_user_groups")
        cursor.execute("CREATE TABLE vicidial_user_groups (user_group VARCHAR(20) PRIMARY KEY, allowed_campaigns VARCHAR(255)) ENGINE=InnoDB")
        cursor.execute("CREATE TABLE vicidial_users (user VARCHAR(32) PRIMARY KEY, pass VARCHAR(255), full_name VARCHAR(50), user_level INT, user_group VARCHAR(20), active CHAR(1), closer_campaigns VARCHAR(255)) ENGINE=InnoDB")
        cursor.execute("CREATE TABLE phones (extension VARCHAR(20) PRIMARY KEY, dialplan_number VARCHAR(20), voicemail_id VARCHAR(20), server_ip VARCHAR(45), login VARCHAR(20) UNIQUE, pass VARCHAR(255), status VARCHAR(20), active CHAR(1), phone_type VARCHAR(20), fullname VARCHAR(50), company VARCHAR(30), protocol VARCHAR(10), local_gmt VARCHAR(10)) ENGINE=InnoDB")
        cursor.execute("INSERT INTO vicidial_user_groups VALUES ('WEBTEST_SYN',' TEST_SYN -')")
        cursor.execute("INSERT INTO vicidial_users VALUES ('cod00016','redacted','Synthetic Agent',1,'WEBTEST_SYN','N',' -')")
        cursor.execute("INSERT INTO phones VALUES ('6101','6101','6101','127.0.0.1','6101','redacted','ACTIVE','N','SIP','Synthetic Agent','Codestra','SIP','-4.00')")
    connection.commit()
    yield MySQLRepository(connection)
    connection.close()


@pytest.mark.parametrize("stage", ["after_first", "after_second", "readback"])
def test_real_database_failures_leave_no_partial_identity(repository, stage):
    trigger = {"after_first": "BEFORE", "after_second": "AFTER", "readback": "BEFORE"}[stage]
    with repository.db.cursor() as cursor:
        if stage == "readback":
            cursor.execute("CREATE TRIGGER fail_stage BEFORE INSERT ON phones FOR EACH ROW SET NEW.active='Y'")
        else:
            cursor.execute(f"CREATE TRIGGER fail_stage {trigger} INSERT ON phones FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='synthetic failure'")
    repository.db.commit()
    with pytest.raises(Exception):
        repository.provision_agent_disabled(command().agent, "ephemeral-not-evidence")
    assert repository.agent_availability("appolon", "6901")["username_available"] is True
    assert repository.agent_availability("appolon", "6901")["extension_available"] is True
    assert repository.agent("cod00016")["user_id"] == "cod00016"
    with repository.db.cursor() as cursor:
        cursor.execute("DROP TRIGGER fail_stage")
    repository.db.commit()
    actual = repository.provision_agent_disabled(command().agent, "ephemeral-not-evidence")
    assert actual["extension"] == "6901" and actual["active"] == "N"
    with pytest.raises(CollisionError, match="already exists"):
        repository.provision_agent_disabled(command().agent, "replacement-not-used")
    assert repository.agent("appolon")["active"] == "N"


@pytest.mark.parametrize("table", ["vicidial_users", "phones"])
def test_nontransactional_tables_are_rejected_before_write(repository, table):
    with repository.db.cursor() as cursor:
        cursor.execute(f"ALTER TABLE {table} ENGINE=MyISAM")
    repository.db.commit()
    with pytest.raises(RuntimeError, match="transactional InnoDB"):
        repository.provision_agent_disabled(command().agent, "ephemeral-not-evidence")
    assert repository.agent_availability("appolon", "6901")["username_available"] is True
