"""podiumd_minikube.postgres: the init SQL's missing databases and the major migration's pure parts."""

import json

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from conftest import FakeRun

from podiumd_minikube import manifests
from podiumd_minikube import postgres
from podiumd_minikube import process
from podiumd_minikube import provision
from podiumd_minikube.paths import NAMESPACE

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


POD = "postgres-0"
PSQL = ("kubectl", "exec", "-n", NAMESPACE, POD, "--", "psql", "-U", "postgres", "-tA", "-c")


@pytest.fixture
def cluster(fake_run: FakeRun) -> FakeRun:
    """fake_run with a Ready Postgres pod."""
    pod = {"metadata": {"name": POD}, "status": {"conditions": [{"type": "Ready", "status": "True"}]}}
    fake_run.on("kubectl", "get", "pod", "-n", NAMESPACE, "-l", "app=postgres", stdout=json.dumps({"items": [pod]}))
    return fake_run


@pytest.mark.parametrize("step", [postgres.create_missing_databases, postgres.rename_databases, postgres.rename_rows])
def test_steps_skip_a_cluster_without_postgres(fake_run: FakeRun, step: Callable[[], None]):
    fake_run.on("kubectl", "get", "deployment/postgres", returncode=1)
    step()
    assert fake_run.calls == [["kubectl", "get", "deployment/postgres", "-n", NAMESPACE]]


@pytest.mark.parametrize(("databases", "script"), [("a\nPabc\n", "CREATE ROLE b WITH LOGIN"), ("a\nb\nPabc\n", None)])
def test_create_missing_databases_runs_the_missing_part_of_the_init_sql(
    cluster: FakeRun, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, databases: str, script: str | None
):
    monkeypatch.setattr(postgres, "INIT_SQL", tmp_path / "init.sql")
    postgres.INIT_SQL.write_text(INIT_SQL, encoding="utf-8")
    cluster.on(*PSQL, "select datname from pg_database;", stdout=databases)
    cluster.on(*PSQL, "select rolname from pg_roles;", stdout="a\npabc\n")
    postgres.create_missing_databases()
    created = [stdin for call, stdin in zip(cluster.calls, cluster.stdins, strict=True) if "ON_ERROR_STOP=1" in call]
    if script is None:
        assert created == []
    else:
        assert len(created) == 1
        assert created[0] is not None
        assert created[0].startswith(script)


def test_rename_databases_renames_only_an_old_name_without_its_new_one(cluster: FakeRun):
    cluster.on(*PSQL, "select datname from pg_database;", stdout="objects\nobjecten\nobjecttypes\n")
    postgres.rename_databases()
    renames = [call[call.index("-f") - 4 :] for call in cluster.ran("kubectl", "exec", "-i")]
    assert renames == [["-v", "old=objecttypes", "-v", "new=objecttypen", "-f", "-"]]


def test_rename_rows_updates_only_old_values_whose_new_one_is_absent(cluster: FakeRun, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        postgres,
        "RENAMES",
        {
            "db1": {("t", "c"): {"x": "y", "p": "q", "m": "n"}},
            "db2": {("t2", "c2"): {"x": "y"}},
            "absent": {("t3", "c3"): {"x": "y"}},
        },
    )
    cluster.on(*PSQL, "select datname from pg_database;", stdout="db1\ndb2\n")
    db1 = ("kubectl", "exec", "-i", "-n", NAMESPACE, POD, "--", "psql", "-U", "postgres", "-d", "db1", "-tA")
    cluster.on(*db1, "-c", "select to_regclass('t');", stdout="t\n")
    cluster.on(*db1, "-v", "table=t", "-v", "column=c", "-f", "-", stdout="x\np\nq\n")
    postgres.rename_rows()
    updates = [call[-6:] for call in cluster.calls if "ON_ERROR_STOP=1" in call]
    assert updates == [["-v", "old=x", "-v", "new=y", "-f", "-"]]
    assert not [call for call in cluster.calls if "-d" in call and call[call.index("-d") + 1] == "absent"]
    assert not [call for call in cluster.calls if "table=t2" in call]


def _render(major: int) -> manifests.Render:
    def doc(kind: str, name: str) -> dict[str, Any]:
        return {"kind": kind, "metadata": {"name": name}}

    postgres_deployment = doc("Deployment", "postgres") | {
        "spec": {"template": {"spec": {"containers": [{"image": f"postgis/postgis:{major}-3.5"}]}}}
    }
    docs = [postgres_deployment, doc("Deployment", "zac"), doc("StatefulSet", "solr"), doc("Keycloak", "keycloak")]
    return manifests.Render(docs=[*docs, doc("ConfigMap", "x")], large_configmaps=[])


@pytest.mark.parametrize(
    "render", [manifests.Render(docs=[], large_configmaps=[]), _render(16)], ids=["not rendered", "same major"]
)
def test_migrate_major_leaves_postgres_alone(cluster: FakeRun, render: manifests.Render):
    cluster.on(*PSQL, "show server_version_num", stdout="160004\n")
    postgres.migrate_major(render)
    assert not cluster.ran("kubectl", "exec", "-n", NAMESPACE, POD, "--", "pg_dumpall")
    assert not cluster.ran("kubectl", "apply")


def test_migrate_major_dumps_restores_and_restarts_the_clients(
    cluster: FakeRun, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(postgres, "DUMP_DIR", tmp_path / "dumps")
    loaded: list[list[str]] = []
    monkeypatch.setattr(provision, "load_images", loaded.append)
    cluster.on(*PSQL, "show server_version_num", stdout="160004\n")
    cluster.on("kubectl", "exec", "-n", NAMESPACE, POD, "--", "pg_dumpall", stdout="DUMP")
    cluster.on("kubectl", "exec", "-i", "-n", NAMESPACE, POD, "--", "psql", stderr='ERROR:  role "a" already exists\n')
    cluster.on("kubectl", "get", "statefulset/solr", returncode=1)
    postgres.migrate_major(_render(17))
    (dump,) = (tmp_path / "dumps").glob("pg16-*.sql")
    assert dump.read_text(encoding="utf-8") == "DUMP"
    assert loaded == [["postgis/postgis:17-3.5"]]
    applied = [stdin for call, stdin in zip(cluster.calls, cluster.stdins, strict=True) if call[1] == "apply"]
    assert [manifests.name_of(yaml.safe_load(stdin or "")) for stdin in applied] == ["postgres"]
    assert "DUMP" in cluster.stdins
    (cleanup,) = cluster.ran("kubectl", "exec", "-n", NAMESPACE, POD, "--", "find")
    assert cleanup[cleanup.index("-name") + 1] == "pg17"
    assert cluster.ran("kubectl", "rollout", "restart") == [
        ["kubectl", "rollout", "restart", "-n", NAMESPACE, "deployment/zac", "statefulset/keycloak"]
    ]


def test_restore_errors_keep_the_dump_and_the_old_data(cluster: FakeRun, tmp_path: Path):
    dump = tmp_path / "pg16.sql"
    dump.write_text("DUMP", encoding="utf-8")
    cluster.on(
        "kubectl", "exec", "-i", "-n", NAMESPACE, POD, "--", "psql", stderr='ERROR:  relation "x" does not exist\n'
    )
    with pytest.raises(process.UserError, match=r'Postgres 17 had errors \(dump kept in .*pg16.sql\): relation "x"'):
        postgres.restore(dump, 17, _render(17))
    assert not cluster.ran("kubectl", "exec", "-n", NAMESPACE, POD, "--", "find")
