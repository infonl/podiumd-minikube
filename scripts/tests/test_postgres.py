"""lib.postgres: the init SQL's missing databases."""

from lib import postgres

INIT_SQL = """-- comment
CREATE ROLE a WITH LOGIN PASSWORD 'a';
CREATE DATABASE a OWNER a;
CREATE ROLE pabc WITH LOGIN PASSWORD 'pabc';
CREATE DATABASE "Pabc" OWNER pabc;
CREATE ROLE b WITH LOGIN PASSWORD 'b';
CREATE DATABASE b OWNER b;
\\c a
CREATE EXTENSION IF NOT EXISTS postgis;
\\c b
CREATE EXTENSION IF NOT EXISTS postgis;
"""


def test_missing_sql_only_covers_missing_databases():
    missing, script = postgres.missing_sql(INIT_SQL, {"a", "Pabc"}, {"a", "pabc"})
    assert missing == ["b"]
    assert (
        script
        == "CREATE ROLE b WITH LOGIN PASSWORD 'b';\nCREATE DATABASE b OWNER b;\n\\c b\nCREATE EXTENSION IF NOT EXISTS postgis;\n"
    )


def test_missing_sql_keeps_existing_role_and_quoted_names():
    missing, script = postgres.missing_sql(INIT_SQL, {"a", "b"}, {"a", "b", "pabc"})
    assert missing == ["Pabc"]
    assert script == 'CREATE DATABASE "Pabc" OWNER pabc;\n'


def test_missing_sql_nothing_missing():
    assert postgres.missing_sql(INIT_SQL, {"a", "b", "Pabc"}, set()) == ([], "")
