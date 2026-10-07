"""The local CA that signs this cluster's TLS certificates, and the cert-manager issuer for it.

The reference environments use Let's Encrypt; minikube has no public DNS, so
a CA created once per checkout in .pki/ (gitignored) takes its place. Clients
outside the cluster (browsers, podiumd-tests) trust .pki/ca.crt.
"""

import yaml

from lib import kube
from lib import process
from lib.paths import CHART_DIR

PKI_DIR = CHART_DIR / ".pki"
CA_CERT = PKI_DIR / "ca.crt"
CA_KEY = PKI_DIR / "ca.key"
CA_DAYS = 3650

CERT_MANAGER_NAMESPACE = "cert-manager"
# As ExternalsPodiumD (podiumd-infra: v1.13.2).
CERT_MANAGER_VERSION = "v1.12.2"
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


def install_cert_manager() -> None:
    """Installs the pinned cert-manager chart with its CRDs unless it is there."""
    if kube.exists("deployment/cert-manager", CERT_MANAGER_NAMESPACE):
        print(f"cert-manager already installed in namespace '{CERT_MANAGER_NAMESPACE}' - skipping.")
        return
    print(f"Installing cert-manager {CERT_MANAGER_VERSION}...")
    process.run(["helm", "repo", "add", "jetstack", "https://charts.jetstack.io"], check=False)
    process.output(["helm", "repo", "update", "jetstack"])
    process.run(
        ["helm", "upgrade", "--install", "cert-manager", "jetstack/cert-manager", "--version", CERT_MANAGER_VERSION,
         "-n", CERT_MANAGER_NAMESPACE, "--create-namespace", "--set", "installCRDs=true", "--wait", "--timeout=300s"],
        capture=False,
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
    """Applies the CA Secret and ClusterIssuer; idempotent."""
    if ensure_ca():
        print(f"Created the local CA in {PKI_DIR} (trust {CA_CERT} in browsers and test clients).")
    kube.kubectl_shown("apply", "-f", "-", stdin=issuer_manifests())
    kube.kubectl_shown("wait", "--for=condition=Ready", f"clusterissuer/{ISSUER}", "--timeout=120s")
