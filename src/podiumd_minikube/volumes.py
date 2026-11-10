"""A pod's fsGroup on minikube's hostPath volumes, which the kubelet does not apply to them.

The kubelet gives a volume the pod's fsGroup (group, g+rwX, setgid directories) when its root does not have it
(fsGroupChangePolicy OnRootMismatch, as Azure disks get it). A fresh hostPath volume is world-writable, so only
data written under another user needs it: podiumd 4.10 runs frankgateway's etcd as uid 1001, 4.9 as root.
"""

from podiumd_minikube import kube
from podiumd_minikube import manifests

_WORKLOADS = ("Deployment", "StatefulSet")


def wanted(docs: list[manifests.Doc]) -> dict[str, int]:
    """PVC name, or StatefulSet claim prefix ending in "-", -> the fsGroup of the pod template that mounts it."""
    found: dict[str, int] = {}
    for doc in docs:
        if doc.get("kind") in _WORKLOADS:
            found.update(_claim_groups(doc))
    return found


def _claim_groups(workload: manifests.Doc) -> dict[str, int]:
    """The claims and claim prefixes workload mounts -> its pod template's fsGroup; {} without one."""
    spec = manifests.section(workload, "spec")
    pod = manifests.section(manifests.section(spec, "template"), "spec")
    group = manifests.section(pod, "securityContext").get("fsGroup")
    if group is None:
        return {}
    mounted: list[manifests.Doc] = pod.get("volumes") or []
    claims = [str(manifests.section(volume, "persistentVolumeClaim").get("claimName") or "") for volume in mounted]
    templates: list[manifests.Doc] = spec.get("volumeClaimTemplates") or []
    prefixes = [f"{manifests.name_of(template)}-{manifests.name_of(workload)}-" for template in templates]
    return dict.fromkeys([*filter(None, claims), *prefixes], int(group))


def _group_of(claim: str, groups: dict[str, int]) -> int | None:
    if claim in groups:
        return groups[claim]
    prefixes = {prefix: group for prefix, group in groups.items() if prefix.endswith("-")}
    return next((group for prefix, group in prefixes.items() if claim.startswith(prefix)), None)


def _host_path(volume: manifests.Doc) -> str:
    """volume's spec.hostPath.path; "" for other volumes."""
    return str(manifests.section(manifests.section(volume, "spec"), "hostPath").get("path") or "")


def changes(
    groups: dict[str, int],
    claims: list[manifests.Doc],
    volumes: list[manifests.Doc],
) -> dict[str, int]:
    """Host path -> fsGroup for each bound claim of groups on a hostPath volume."""
    paths = {manifests.name_of(volume): _host_path(volume) for volume in volumes}
    found: dict[str, int] = {}
    for claim in claims:
        group = _group_of(manifests.name_of(claim), groups)
        path = paths.get(str(manifests.section(claim, "spec").get("volumeName") or ""))
        if group is not None and path:
            found[path] = group
    return found


def script(found: dict[str, int]) -> str:
    """The node commands that give each path its group, as the kubelet's OnRootMismatch; prints each changed path."""
    return "\n".join(
        f'[ "$(sudo stat -c %g {path})" = {group} ] || {{ sudo chgrp -R {group} {path}'
        f" && sudo chmod -R g+rwX {path} && sudo find {path} -type d -exec chmod g+s {{}} + && echo {path}; }}"
        for path, group in sorted(found.items())
    )


def apply_fs_groups(docs: list[manifests.Doc], namespace: str) -> None:
    """Gives the existing hostPath volumes of docs' pod templates in namespace their fsGroup."""
    if not (groups := wanted(docs)):
        return
    claims = kube.get_json("pvc", "-n", namespace)["items"]
    if not (found := changes(groups, claims, kube.get_json("pv")["items"])):
        return
    if changed := kube.node(script(found)).split():
        print(
            f"Gave {len(changed)} volume(s) their pod's fsGroup (minikube's hostPath ignores it): {' '.join(changed)}"
        )
