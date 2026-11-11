"""podiumd_minikube.postgres: the init SQL's missing databases and the major migration's pure parts."""

import json

from pathlib import Path

import pytest
import yaml

from conftest import FakeRun

from podiumd_minikube import manifests
from podiumd_minikube import postgres
from podiumd_minikube import process
from podiumd_minikube import provision
from podiumd_minikube.objects import Doc
from podiumd_minikube.paths import NAMESPACE

INIT_SQL = r"""-- comment
CREATE ROLE a WITH LOGIN PASSWORD 'a';
CREATE DATABASE a;
CREATE ROLE b WITH LOGIN PASSWORD 'b';
CREATE DATABASE b;
\c a
CREATE EXTENSION IF NOT EXISTS postgis;
\c b
CREATE EXTENSION IF NOT EXISTS postgis;
\c b
GRANT ALL ON DATABASE b TO b;
"""


def test_missing_sql_only_covers_missing_databases():
    missing, script = postgres.missing_sql(INIT_SQL, {"a"}, {"a"})
    assert missing == ["b"]
    expected = r"""CREATE ROLE b WITH LOGIN PASSWORD 'b';
CREATE DATABASE b;
\c b
CREATE EXTENSION IF NOT EXISTS postgis;
\c b
GRANT ALL ON DATABASE b TO b;
"""
    assert script == expected


def test_missing_sql_keeps_an_existing_role():
    missing, script = postgres.missing_sql(INIT_SQL, {"a"}, {"a", "b"})
    assert missing == ["b"]
    assert script.startswith("CREATE DATABASE b;\n")


def test_missing_sql_nothing_missing():
    assert postgres.missing_sql(INIT_SQL, {"a", "b"}, set()) == ([], "")


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
PSQL = ("kubectl", "exec", "-i", "-n", NAMESPACE, POD, "--", "psql", "-U", "postgres")


def _server(owners: dict[str, str] | None = None, roles: set[str] | None = None, major: int = 16) -> postgres.Server:
    return postgres.Server(POD, major, owners or {}, roles or set())


@pytest.fixture
def cluster(fake_run: FakeRun) -> FakeRun:
    """fake_run with a Ready Postgres pod."""
    pod = {"metadata": {"name": POD}, "status": {"conditions": [{"type": "Ready", "status": "True"}]}}
    fake_run.on("kubectl", "get", "pod", "-n", NAMESPACE, "-l", "app=postgres", stdout=json.dumps({"items": [pod]}))
    return fake_run


def test_prepare_skips_a_cluster_without_postgres(fake_run: FakeRun):
    fake_run.on("kubectl", "get", "deployment/postgres", returncode=1)
    postgres.prepare(_render(16))
    assert fake_run.calls == [["kubectl", "get", "deployment/postgres", "-n", NAMESPACE]]


def test_server_reads_major_owners_and_roles_with_one_query(cluster: FakeRun):
    rows = "major|15\ndatabase|openzaak|postgres\ndatabase|objects|objects\nrole|openzaak\nrole|objects\n"
    cluster.on(*PSQL, "-tA", "-c", postgres.FACTS_SQL, stdout=rows)
    assert postgres.server() == postgres.Server(
        POD, 15, {"openzaak": "postgres", "objects": "objects"}, {"openzaak", "objects"}
    )


@pytest.mark.parametrize(("facts", "reads"), [("major|16\n", 1), ("major|16\ndatabase|objecttypes|objecttypes\n", 2)])
def test_prepare_reads_the_server_again_only_after_a_change(
    cluster: FakeRun, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, facts: str, reads: int
):
    monkeypatch.setattr(postgres, "INIT_SQL", tmp_path / "init.sql")
    postgres.INIT_SQL.write_text("", encoding="utf-8")
    cluster.on("kubectl", "get", "deployment/postgres")
    cluster.on(*PSQL, "-tA", "-c", postgres.FACTS_SQL, stdout=facts)
    postgres.prepare(_render(16))
    assert len(cluster.ran(*PSQL, "-tA", "-c", postgres.FACTS_SQL)) == reads


@pytest.mark.parametrize(("databases", "script"), [("a\n", "CREATE ROLE b WITH LOGIN"), ("a\nb\n", None)])
def test_create_missing_databases_runs_the_missing_part_of_the_init_sql(
    cluster: FakeRun, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, databases: str, script: str | None
):
    monkeypatch.setattr(postgres, "INIT_SQL", tmp_path / "init.sql")
    postgres.INIT_SQL.write_text(INIT_SQL, encoding="utf-8")
    current = _server(dict.fromkeys(databases.split(), "postgres"), {"a"})
    assert bool(postgres.create_missing_databases(current)) == (script is not None)
    created = [stdin for call, stdin in zip(cluster.calls, cluster.stdins, strict=True) if "ON_ERROR_STOP=1" in call]
    if script is None:
        assert created == []
    else:
        assert len(created) == 1
        assert created[0] is not None
        assert created[0].startswith(script)


def test_rename_databases_renames_only_an_old_name_without_its_new_one(cluster: FakeRun):
    owners = dict.fromkeys(["objects", "objecten", "objecttypes"], "postgres")
    assert postgres.rename_databases(_server(owners)) == ["objecttypes"]
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
    # db2's table does not exist yet: psql prints no row for it.
    cluster.on(*PSQL, "-q", "-tA", stdout="db1|t|c|x p q\n")
    postgres.rename_rows(_server(dict.fromkeys(["db1", "db2"], "postgres")))
    script = cluster.stdins[cluster.calls.index(cluster.ran(*PSQL, "-q", "-tA")[0])]
    assert script is not None
    assert r"\c db2" in script
    assert "absent" not in script
    updates = [call[-6:] for call in cluster.ran(*PSQL, "-d")]
    assert updates == [["-v", "old=x", "-v", "new=y", "-f", "-"]]


def _render(major: int) -> manifests.Render:
    def doc(kind: str, name: str) -> Doc:
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
    assert postgres.migrate_major(render, _server()) is None
    assert not cluster.ran("kubectl", "exec", "-n", NAMESPACE, POD, "--", "pg_dumpall")
    assert not cluster.ran("kubectl", "apply")


def test_migrate_major_dumps_restores_and_restarts_the_clients(
    cluster: FakeRun, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(postgres, "DUMP_DIR", tmp_path / "dumps")
    loaded: list[list[str]] = []
    monkeypatch.setattr(provision, "load_images", loaded.append)
    cluster.on("kubectl", "exec", "-n", NAMESPACE, POD, "--", "pg_dumpall", stdout="DUMP")
    cluster.on("kubectl", "exec", "-i", "-n", NAMESPACE, POD, "--", "psql", stderr='ERROR:  role "a" already exists\n')
    cluster.on("kubectl", "get", "statefulset/solr", returncode=1)
    assert postgres.migrate_major(_render(17), _server()) is not None
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


def test_align_with_azure_moves_app_databases_and_installs_the_allow_list_everywhere(cluster: FakeRun):
    postgres.align_with_azure(_server({"template1": "postgres", "openzaak": "openzaak"}))
    [script] = [stdin for call, stdin in zip(cluster.calls, cluster.stdins, strict=True) if call[-2:] == ["-f", "-"]]
    assert script is not None
    assert script.count("CREATE EVENT TRIGGER azure_extension_allow_list") == 2
    assert "ALTER DATABASE openzaak OWNER TO postgres;" in script
    assert "GRANT ALL ON SCHEMA public TO openzaak;" in script
    assert "ALTER DATABASE template1" not in script
    tail = r"""\c openzaak
DROP EXTENSION IF EXISTS "uuid-ossp";"""
    assert script.endswith(tail)
