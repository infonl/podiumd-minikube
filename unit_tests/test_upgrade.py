"""upgrade: the chart's fixed-name Jobs against ExternalsPodiumD's delete list."""

from podiumd_minikube import upgrade


def job(name: str, **metadata: object) -> dict[str, object]:
    return {"kind": "Job", "metadata": {"name": name, **metadata}}


def test_undeleted_fixed_jobs_skips_hooks_ours_varying_names_and_externals_list():
    docs = [
        job("openbeheer-config"),
        job("openzaak-config"),
        job("openvtb-config", annotations={"helm.sh/hook": "post-install,post-upgrade"}),
        job("openvtb-create-omc-token", labels={"app.kubernetes.io/part-of": "podiumd-minikube"}),
        job("import-podiumd-realm-job-cce912a3db"),
        job("keycloak-repair-client-scopes-1"),
        {"kind": "Deployment", "metadata": {"name": "fixed"}},
    ]
    assert upgrade.undeleted_fixed_jobs(docs) == ["openbeheer-config"]
