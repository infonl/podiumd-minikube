"""podiumd_minikube.postgres: the init SQL's missing databases and the major migration's pure parts."""

from podiumd_minikube import postgres

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


def test_major_of_a_postgis_image():
    assert postgres.major_of("postgis/postgis:16-3.5") == 16


def test_restore_errors_skip_what_the_init_sql_created():
    stderr = (
        'psql:<stdin>:12: ERROR:  role "zac" already exists\n'
        'psql:<stdin>:20: ERROR:  unrecognized configuration parameter "transaction_timeout"\n'
        'psql:<stdin>:99: ERROR:  relation "x" does not exist\n'
    )
    assert postgres.restore_errors(stderr) == ['relation "x" does not exist']
