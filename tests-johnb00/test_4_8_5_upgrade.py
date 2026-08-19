"""
Regression tests for what actually changed going from chart 4.8.4 to 4.8.5
(see docs/_UPGRADE_PATHS/4.8.4-to-4.8.5-{upgrade,values-deltas}.md in the
Dimpact-Samenwerking/helm-charts repo, and this session's own migration
notes). This is deliberately narrow to the 4.8.4->4.8.5 hop, not the wider
4.7.3->4.8.5 jump johnb00 actually made in one go - those are covered
elsewhere (test_login_flow.py / test_pkce.py / test_pods.py for PABC,
which became default-enabled back in 4.8.0).

Per that upgrade doc, 4.8.5 changes exactly two component versions and
adds two ZAC chart keys plus hardened default security contexts:

  - Open Beheer 0.9.0 -> 0.9.1 (no schema/config change, UI/session fixes)
  - ZAC (zaakafhandelcomponent) chart 1.0.251 -> 1.0.297, image 5.0.1 -> 5.0.2
    - zac.auth.enablePkce: new chart key, but a documented no-op with the
      5.0.2 image (ZAC gained real PKCE support only in the 5.1.x line) -
      must stay off.
    - zac.brpApi.protocollering.verwerking.extendWithZaaktype: new,
      functional key - only matters for iConnect gemeenten (johnb00 has
      protocollering disabled entirely, so this is a no-op here too, but
      the guard test below confirms that's still true).
    - New hardened pod/container securityContext defaults on the ZAC main
      container, the OPA sidecar, and the Gotenberg (office-converter)
      sidecar - confirmed live below, not just "pods didn't crash".
"""

import json
import subprocess

import pytest

from conftest import NAMESPACE, kubectl

# johnb00's ACR pull-through mirror rewrites every image to digest-only
# (drops the tag entirely - confirmed live: "ghcr.io/infonl/
# zaakafhandelcomponent:5.0.2@sha256:e334f..." becomes
# "podiumdregistry.azurecr.io/ghcr/infonl/zaakafhandelcomponent@sha256:
# e334f..." with no ":5.0.2" left anywhere in the running pod spec), so the
# only thing checkable live is the digest, not the tag string.
EXPECTED_ZAC_IMAGE_DIGEST = (
    "sha256:e334f327008f99304cd545e61f528a44d583162c9bb67f4a589479248a93df3c"
)
EXPECTED_OPENBEHEER_IMAGE_TAG = "0.9.1"

EXPECTED_HARDENED_CONTAINER_SECURITY_CONTEXT = {
    "allowPrivilegeEscalation": False,
    "readOnlyRootFilesystem": True,
}


def _pod_json(pod_name_prefix):
    """First pod whose name starts with the given prefix, as parsed JSON."""
    raw = kubectl("get", "pods", "-n", NAMESPACE, "-o", "json")
    for item in json.loads(raw)["items"]:
        name = item["metadata"]["name"]
        if name == pod_name_prefix or name.startswith(pod_name_prefix + "-"):
            return item
    pytest.skip(f"no pod found matching '{pod_name_prefix}'")


def _container(pod, container_name):
    for c in pod["spec"]["containers"]:
        if c["name"] == container_name:
            return c
    pytest.fail(f"container '{container_name}' not found in pod {pod['metadata']['name']}")


def _configmap_data(name):
    raw = kubectl("get", "configmap", name, "-n", NAMESPACE, "-o", "jsonpath={.data}")
    return json.loads(raw) if raw else {}


def test_zac_image_pinned_to_5_0_2():
    """4.8.5 keeps the ZAC image on 5.0.2 (a same-line patch of 5.0.1),
    deliberately NOT the 5.5.0 the chart's own bundled defaults are tested
    against - see the upgrade doc's own rationale (chart line moves faster
    than the pinned image this hop)."""
    pod = _pod_json("zac")
    zac = _container(pod, "zac")
    assert zac["image"].endswith(f"@{EXPECTED_ZAC_IMAGE_DIGEST}"), (
        f"expected zac image digest {EXPECTED_ZAC_IMAGE_DIGEST} (= app 5.0.2), "
        f"got {zac['image']}"
    )


def test_openbeheer_image_pinned_to_0_9_1():
    pod = _pod_json("openbeheer")
    # openbeheer's main container shares the app's own name in this chart.
    container = _container(pod, "openbeheer")
    assert f":{EXPECTED_OPENBEHEER_IMAGE_TAG}" in container["image"], (
        f"expected openbeheer image tag {EXPECTED_OPENBEHEER_IMAGE_TAG}, "
        f"got {container['image']}"
    )


@pytest.mark.parametrize(
    "pod_prefix,container_name",
    [
        ("zac", "zac"),
        ("zac", "opa"),
        ("zac-office-converter", "office-converter"),
    ],
)
def test_security_context_hardened(pod_prefix, container_name):
    """4.8.5's chart bump adds hardened securityContext defaults
    (seccompProfile, no privilege escalation, read-only rootfs, drop ALL
    capabilities) to the ZAC pod, its OPA sidecar, and the Gotenberg
    sidecar. podiumd doesn't override any of these, so they should apply
    as-is - confirmed live here, not just inferred from "the pod is
    Running" (a read-only-rootfs regression can crash-loop a container
    that previously wrote to disk, which is exactly the failure mode the
    upgrade doc's own verification checklist calls out)."""
    pod = _pod_json(pod_prefix)
    container = _container(pod, container_name)
    sc = container.get("securityContext") or {}
    for key, expected in EXPECTED_HARDENED_CONTAINER_SECURITY_CONTEXT.items():
        assert sc.get(key) == expected, (
            f"{pod_prefix}/{container_name}: expected securityContext.{key}="
            f"{expected}, got {sc.get(key)!r} (full: {sc})"
        )
    capabilities = sc.get("capabilities", {})
    assert capabilities.get("drop") == ["ALL"], (
        f"{pod_prefix}/{container_name}: expected capabilities.drop=['ALL'], "
        f"got {capabilities!r}"
    )
    pod_security_context = pod["spec"].get("securityContext") or {}
    assert pod_security_context.get("seccompProfile", {}).get("type") == "RuntimeDefault", (
        f"{pod_prefix}: expected pod-level seccompProfile.type=RuntimeDefault, "
        f"got {pod_security_context}"
    )


def test_auth_enable_pkce_stays_off():
    """zac.auth.enablePkce (new chart key in 1.0.297) is a documented no-op
    with the pinned 5.0.2 image - ZAC itself has no PKCE handling until the
    5.1.x line (confirmed directly against the v5.0.1/v5.0.2 app source per
    the upgrade doc). Guard against it being flipped on under the mistaken
    impression that it does something on this image."""
    zac_config = _configmap_data("zac")
    assert zac_config.get("AUTH_ENABLE_PKCE") == "false", (
        "AUTH_ENABLE_PKCE is not 'false' - if this was intentionally "
        "enabled, confirm the ZAC image has actually been bumped past "
        "5.0.2 first; enabling it against 5.0.2 is a documented no-op"
    )


def test_brp_protocollering_extend_with_zaaktype_not_silently_iconnect():
    """extendWithZaaktype (new in chart 1.0.297) only matters when BRP
    protocollering is enabled for an iConnect gemeente - it must be true
    there, false for eServices/2Secure. johnb00 has protocollering disabled
    entirely (no vendor configured), so this just guards that state hasn't
    silently drifted into a half-configured iConnect setup."""
    zac_config = _configmap_data("zac")
    protocollering_enabled = zac_config.get("BRP_PROTOCOLLERING_ENABLED")
    assert protocollering_enabled == "false", (
        f"expected BRP_PROTOCOLLERING_ENABLED=false on johnb00 (no BRP vendor "
        f"configured), got {protocollering_enabled!r} - if protocollering was "
        f"deliberately enabled, verify extendWithZaaktype matches the vendor "
        f"(true for iConnect, false for eServices/2Secure)"
    )
