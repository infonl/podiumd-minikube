"""The local CA that signs this cluster's TLS certificates, and the cert-manager issuer for it.

The reference environments use Let's Encrypt; minikube has no public DNS, so
a CA created once per checkout in .pki/ (gitignored) takes its place. Clients
outside the cluster (browsers, podiumd-tests) trust .pki/ca.crt.
"""

import base64
import json
import tempfile

from pathlib import Path
from typing import Any

import certifi
import yaml

from podiumd_minikube import kube
from podiumd_minikube import memory
from podiumd_minikube import polling
from podiumd_minikube import process
from podiumd_minikube.paths import CHART_DIR
from podiumd_minikube.paths import EDGE_NAMESPACE
from podiumd_minikube.paths import RELEASE_NAME

PKI_DIR = CHART_DIR / ".pki"
CA_CERT = PKI_DIR / "ca.crt"
CA_KEY = PKI_DIR / "ca.key"
CA_DAYS = 3650

# As ExternalsPodiumD; OLD_CERT_MANAGER_NAMESPACE is where clusters provisioned before 2026-10-13 have it.
CERT_MANAGER_NAMESPACE = EDGE_NAMESPACE
OLD_CERT_MANAGER_NAMESPACE = "cert-manager"
# As ExternalsPodiumD (pipelines/includes/haven/cert-manager.yml): this chart, with the images it pins.
CERT_MANAGER_VERSION = "v1.20.3"
CERT_MANAGER_IMAGE_TAG = "v1.19.3"
ISSUER = "podiumd-local-ca"


def ensure_ca() -> bool:
    """Creates .pki/ca.key and .pki/ca.crt unless both exist; True when created."""
    if CA_CERT.is_file() and CA_KEY.is_file():
        return False
    PKI_DIR.mkdir(mode=0o700, exist_ok=True)
    process.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:4096", "-nodes", "-sha256",
         "-days", str(CA_DAYS), "-subj", "/CN=podiumd-minikube local CA",
         "-addext", "basicConstraints=critical,CA:TRUE",
         "-addext", "keyUsage=critical,keyCertSign,cRLSign",
         "-keyout", str(CA_KEY), "-out", str(CA_CERT)],
    )  # fmt: skip
    CA_KEY.chmod(0o600)
    return True


def cert_manager_values() -> dict[str, Any]:
    """ExternalsPodiumD's pipelines/values/cert-manager.yml without its AKS parts (ACR images, nodeSelector)."""
    # The cainjector caches every Secret and CRD it watches: 98 MiB at the Go default GOGC 100.
    go_env = {"GOGC": "50", **memory.GO_NO_HUGE_PAGES}

    def component(request: str, limit: str) -> dict[str, Any]:
        return {
            "image": {"tag": CERT_MANAGER_IMAGE_TAG},
            "replicaCount": 1,
            "resources": {"requests": {"cpu": "10m", "memory": request}, "limits": {"cpu": "20m", "memory": limit}},
            "extraEnv": [{"name": name, "value": value} for name, value in go_env.items()],
        }

    return {
        "crds": {"enabled": True},
        **component("48Mi", "128Mi"),
        "config": {
            "apiVersion": "controller.config.cert-manager.io/v1alpha1",
            "kind": "ControllerConfiguration",
            "enableGatewayAPI": True,
        },
        "webhook": component("32Mi", "64Mi"),
        "cainjector": component("96Mi", "256Mi"),
        "acmesolver": {"image": {"tag": CERT_MANAGER_IMAGE_TAG}},
        "startupapicheck": {"enabled": False},
    }


def _move_from_old_namespace() -> None:
    """Uninstalls a cert-manager release in OLD_CERT_MANAGER_NAMESPACE, keeping its CRDs and so every Certificate.

    The CRDs get the new release's ownership, else helm refuses to adopt them.
    """
    if not process.output(["helm", "list", "-n", OLD_CERT_MANAGER_NAMESPACE, "-q", "-f", "^cert-manager$"]).strip():
        return
    print(f"Moving cert-manager from namespace '{OLD_CERT_MANAGER_NAMESPACE}' to '{CERT_MANAGER_NAMESPACE}'...")
    kube.kubectl(
        "annotate", "crd", "-l", "app.kubernetes.io/instance=cert-manager", "--overwrite",
        "helm.sh/resource-policy=keep", f"meta.helm.sh/release-namespace={CERT_MANAGER_NAMESPACE}",
    )  # fmt: skip
    process.run(["helm", "uninstall", "cert-manager", "-n", OLD_CERT_MANAGER_NAMESPACE, "--wait"], capture=False)
    kube.kubectl("delete", "namespace", OLD_CERT_MANAGER_NAMESPACE, "--wait=true")


def _installed() -> bool:
    """True when the cert-manager release runs CERT_MANAGER_VERSION with cert_manager_values()."""
    releases = json.loads(process.output(["helm", "list", "-n", CERT_MANAGER_NAMESPACE, "-o", "json"]))
    if not any(release["chart"] == f"cert-manager-{CERT_MANAGER_VERSION}" for release in releases):
        return False
    values = process.output(["helm", "get", "values", "cert-manager", "-n", CERT_MANAGER_NAMESPACE, "-o", "json"])
    return json.loads(values) == cert_manager_values()


def install_cert_manager() -> None:
    """Installs or upgrades cert-manager to CERT_MANAGER_VERSION and its values; skips it when both run."""
    _move_from_old_namespace()
    if _installed():
        print(f"cert-manager {CERT_MANAGER_VERSION} already installed in '{CERT_MANAGER_NAMESPACE}' - skipping.")
        return
    print(f"Installing cert-manager {CERT_MANAGER_VERSION}...")
    process.run(["helm", "repo", "add", "jetstack", "https://charts.jetstack.io"], check=False)
    process.fetch(["helm", "repo", "update", "jetstack"])
    process.fetch(
        ["helm", "upgrade", "--install", "cert-manager", "jetstack/cert-manager", "--version", CERT_MANAGER_VERSION,
         "-n", CERT_MANAGER_NAMESPACE, "--create-namespace", "--reset-values", "--rollback-on-failure",
         "--server-side=false", "--wait", "--timeout=10m", "-f", "-"],
        stdin=yaml.safe_dump(cert_manager_values()),
        shown=True,
    )  # fmt: skip


def issuer_manifests() -> str:
    """The CA Secret (from .pki/) and the ClusterIssuer that signs with it."""
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "kubernetes.io/tls",
        "metadata": {"name": ISSUER, "namespace": CERT_MANAGER_NAMESPACE},
        "stringData": {
            "tls.crt": CA_CERT.read_text(encoding="utf-8"),
            "tls.key": CA_KEY.read_text(encoding="utf-8"),
        },
    }
    issuer = {
        "apiVersion": "cert-manager.io/v1",
        "kind": "ClusterIssuer",
        "metadata": {"name": ISSUER},
        "spec": {"ca": {"secretName": ISSUER}},
    }
    return yaml.safe_dump_all([secret, issuer], sort_keys=False)


def install_issuer() -> None:
    """Applies the CA Secret and ClusterIssuer; idempotent.

    Retried: after cert-manager's pods are replaced, its webhook has no CA until
    the new cainjector takes over the old leader lease (75 s after a move).
    """
    if ensure_ca():
        print(f"Created the local CA in {PKI_DIR} (trust {CA_CERT} in browsers and test clients).")
    manifests = issuer_manifests()

    def applied() -> bool:
        return process.run(["kubectl", "apply", "-f", "-"], stdin=manifests, check=False).returncode == 0

    if not polling.wait_until(applied, timeout=180, interval=5):
        kube.kubectl_shown("apply", "-f", "-", stdin=manifests)
    kube.kubectl_shown("wait", "--for=condition=Ready", f"clusterissuer/{ISSUER}", "--timeout=120s")


# The CA as pods use it: ConfigMap TRUST_CONFIGMAP mounted at TRUST_DIR in every container.
TRUST_CONFIGMAP = "podiumd-ca"
TRUST_DIR = "/etc/podiumd-ca"
TRUSTSTORE_PASSWORD = "changeit"  # nosec B105  # noqa: S105 - Java's default truststore password
TRUST_ENV = {
    # OpenSSL (Python ssl, httpx, .NET on Linux) and requests: public CAs plus ours.
    "SSL_CERT_FILE": f"{TRUST_DIR}/ca-bundle.pem",
    "REQUESTS_CA_BUNDLE": f"{TRUST_DIR}/ca-bundle.pem",
    "NODE_EXTRA_CA_CERTS": f"{TRUST_DIR}/ca.crt",
    "JAVA_TOOL_OPTIONS": (
        f"-Djavax.net.ssl.trustStore={TRUST_DIR}/truststore.p12 -Djavax.net.ssl.trustStoreType=PKCS12 "
        f"-Djavax.net.ssl.trustStorePassword={TRUSTSTORE_PASSWORD}"
    ),
}


def trust_configmap(namespace: str) -> str:
    """ConfigMap TRUST_CONFIGMAP: ca.crt, ca-bundle.pem (certifi's public CAs plus ours) and a Java truststore."""
    bundle = Path(certifi.where()).read_text(encoding="utf-8") + CA_CERT.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as directory:
        bundle_file, truststore = Path(directory) / "bundle.pem", Path(directory) / "truststore.p12"
        bundle_file.write_text(bundle, encoding="utf-8")
        # -jdktrust marks the certificates as trusted for Java (OpenSSL >= 3.2).
        process.run(
            ["openssl", "pkcs12", "-export", "-nokeys", "-jdktrust", "anyExtendedKeyUsage",
             "-in", str(bundle_file), "-out", str(truststore), "-passout", f"pass:{TRUSTSTORE_PASSWORD}"],
        )  # fmt: skip
        p12 = base64.b64encode(truststore.read_bytes()).decode()
    configmap = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": TRUST_CONFIGMAP,
            "namespace": namespace,
            "labels": {"app.kubernetes.io/instance": RELEASE_NAME},
        },
        "data": {"ca.crt": CA_CERT.read_text(encoding="utf-8"), "ca-bundle.pem": bundle},
        "binaryData": {"truststore.p12": p12},
    }
    return yaml.safe_dump(configmap, sort_keys=False)


def apply_trust(namespace: str) -> None:
    """Applies TRUST_CONFIGMAP (server-side: it exceeds client-side apply's annotation limit)."""
    kube.kubectl_shown(
        "apply", "--server-side", "--force-conflicts", "-n", namespace, "-f", "-", stdin=trust_configmap(namespace)
    )
