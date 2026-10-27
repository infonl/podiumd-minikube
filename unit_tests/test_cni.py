"""podiumd_minikube.cni: writing and applying the Cilium manifest."""

from pathlib import Path

import pytest

from conftest import FakeRun

from podiumd_minikube import cni


def test_write_manifest_writes_the_render_for_minikube_start(
    fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(cni, "MANIFEST", tmp_path / ".cni" / "cilium.yaml")
    monkeypatch.setattr(cni, "CHART", tmp_path / ".cni" / "cilium.tgz")
    fake_run.on("helm", "template", stdout="kind: ConfigMap\nmetadata:\n  name: cilium-config\n")
    assert cni.write_manifest() == str(cni.MANIFEST)
    assert "name: cilium-config" in cni.MANIFEST.read_text(encoding="utf-8")
    assert fake_run.ran("helm", "pull", "cilium")
    cni.CHART.write_text("")
    cni.write_manifest()
    assert len(fake_run.ran("helm", "pull")) == 1
    assert fake_run.ran("helm", "template")[-1][:3] == ["helm", "template", "cilium"]
    assert fake_run.ran("helm", "template")[-1][3] == str(cni.CHART)


def test_apply_applies_the_manifest_and_waits_for_the_agent(fake_run: FakeRun):
    fake_run.on("kubectl", "get", "daemonset/kindnet", returncode=1)
    cni.apply()
    assert fake_run.calls[1:] == [
        ["kubectl", "apply", "-f", str(cni.MANIFEST)],
        ["kubectl", "rollout", "status", "-n", "kube-system", "daemonset/cilium", "--timeout=300s"],
    ]


def test_values_give_the_agent_gogc_50():
    assert {"name": "GOGC", "value": "50"} in cni.values()["extraEnv"]
