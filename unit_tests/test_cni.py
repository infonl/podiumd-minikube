"""podiumd_minikube.cni: writing and applying the Cilium manifest."""

from pathlib import Path

import pytest

from conftest import FakeRun

from podiumd_minikube import cni


def test_write_manifest_writes_the_render_for_minikube_start(
    fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(cni, "MANIFEST", tmp_path / ".cni" / "cilium.yaml")
    fake_run.on("helm", "template", stdout="kind: ConfigMap\nmetadata:\n  name: cilium-config\n")
    assert cni.write_manifest() == str(cni.MANIFEST)
    assert "name: cilium-config" in cni.MANIFEST.read_text(encoding="utf-8")


def test_apply_applies_the_manifest_and_waits_for_the_agent(fake_run: FakeRun):
    fake_run.on("kubectl", "get", "daemonset/kindnet", returncode=1)
    cni.apply()
    assert fake_run.calls[1:] == [
        ["kubectl", "apply", "-f", str(cni.MANIFEST)],
        ["kubectl", "rollout", "status", "-n", "kube-system", "daemonset/cilium", "--timeout=300s"],
    ]
