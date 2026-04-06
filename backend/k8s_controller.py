"""Kubernetes controller: create/delete/status pods and services for user containers.

All kubernetes API calls use the synchronous client wrapped in asyncio.to_thread
to avoid blocking the FastAPI event loop.

Each user gets an isolated namespace: kuboco-user-{user_id}.  The namespace
(plus its NetworkPolicies) is created on-demand the first time a container is
spawned for that user.
"""

import asyncio
import logging
from typing import Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from backend.config import settings

logger = logging.getLogger(__name__)


def _pod_name(user_id: int, container_id: int) -> str:
    return f"kuboco-{user_id}-{container_id}"


def _svc_name(user_id: int, container_id: int) -> str:
    return f"kuboco-svc-{user_id}-{container_id}"


def _netpol_name(user_id: int, container_id: int) -> str:
    return f"kuboco-netpol-{user_id}-{container_id}"


def _user_namespace(user_id: int) -> str:
    return f"kuboco-user-{user_id}"


def user_namespace_name(user_id: int) -> str:
    """Return the deterministic namespace name for a user (does not create it)."""
    return _user_namespace(user_id)


def get_svc_dns(user_id: int, container_id: int, namespace: str) -> str:
    return (
        f"kuboco-svc-{user_id}-{container_id}"
        f".{namespace}.svc.cluster.local"
    )


def _load_k8s_config() -> None:
    try:
        config.load_incluster_config()
        logger.debug("Using in-cluster Kubernetes config")
    except config.ConfigException:
        try:
            config.load_kube_config(config_file=settings.kubeconfig_path)
            logger.debug("Using kubeconfig file")
        except config.ConfigException as exc:
            raise RuntimeError(
                "Cannot load Kubernetes configuration. "
                "Running inside a cluster or set KUBECONFIG_PATH."
            ) from exc


def _get_v1() -> client.CoreV1Api:
    _load_k8s_config()
    return client.CoreV1Api()


def _get_custom_api() -> client.CustomObjectsApi:
    _load_k8s_config()
    return client.CustomObjectsApi()


def _get_networking_v1() -> client.NetworkingV1Api:
    _load_k8s_config()
    return client.NetworkingV1Api()


def _build_cilium_netpol(
    netpol_name: str,
    pod_name: str,
    namespace: str,
    policy: "ImageNetworkPolicy",  # backend.config.ImageNetworkPolicy
) -> dict:
    """Build a CiliumNetworkPolicy manifest for a pod.

    Egress: allow everything except the explicitly denied FQDNs.
    Ingress is controlled at the namespace level by the deny-all-ingress and
    allow-from-backend standard NetworkPolicies, so no ingress rules are set here.
    """
    spec: dict = {
        "endpointSelector": {"matchLabels": {"pod-name": pod_name}},
        "egress": [{}],  # allow all egress not explicitly denied
    }

    if policy.egress_deny_fqdns:
        to_fqdns = [
            {"matchPattern": fqdn} if "*" in fqdn else {"matchName": fqdn}
            for fqdn in policy.egress_deny_fqdns
        ]
        spec["egressDeny"] = [{"toFQDNs": to_fqdns}]

    return {
        "apiVersion": "cilium.io/v2",
        "kind": "CiliumNetworkPolicy",
        "metadata": {"name": netpol_name, "namespace": namespace},
        "spec": spec,
    }


def _build_pod(
    pod_name: str,
    user_id: int,
    container_id: int,
    image: str,
    namespace: str,
) -> client.V1Pod:
    labels = {
        "app": "kuboco-container",
        "user-id": str(user_id),
        "container-id": str(container_id),
        "pod-name": pod_name,
    }
    return client.V1Pod(
        metadata=client.V1ObjectMeta(
            name=pod_name,
            namespace=namespace,
            labels=labels,
        ),
        spec=client.V1PodSpec(
            restart_policy="Never",
            containers=[
                client.V1Container(
                    name="shell",
                    image=image,
                    image_pull_policy="IfNotPresent",
                    ports=[
                        client.V1ContainerPort(
                            container_port=settings.ttyd_port,
                            name="ttyd",
                            protocol="TCP",
                        )
                    ],
                    resources=client.V1ResourceRequirements(
                        requests={
                            "cpu": settings.pod_cpu_request,
                            "memory": settings.pod_memory_request,
                        },
                        limits={
                            "cpu": settings.pod_cpu_limit,
                            "memory": settings.pod_memory_limit,
                        },
                    ),
                    security_context=client.V1SecurityContext(
                        allow_privilege_escalation=False,
                        capabilities=client.V1Capabilities(drop=["ALL"]),
                    ),
                )
            ],
        ),
    )


def _build_service(
    svc_name: str,
    pod_name: str,
    namespace: str,
) -> client.V1Service:
    return client.V1Service(
        metadata=client.V1ObjectMeta(
            name=svc_name,
            namespace=namespace,
            labels={
                "app": "kuboco-container",
                "svc-name": svc_name,
            },
        ),
        spec=client.V1ServiceSpec(
            type="ClusterIP",
            selector={"pod-name": pod_name},
            ports=[
                client.V1ServicePort(
                    name="ttyd",
                    port=settings.ttyd_port,
                    target_port=settings.ttyd_port,
                    protocol="TCP",
                )
            ],
        ),
    )


# --------------------------------------------------------------------------- #
# Namespace bootstrap
# --------------------------------------------------------------------------- #

def _sync_ensure_user_namespace(user_id: int) -> str:
    """Create the per-user namespace and its NetworkPolicies if they don't exist.

    Returns the namespace name.  Safe to call repeatedly — 409 Conflict errors
    are silently ignored.
    """
    ns_name = _user_namespace(user_id)
    v1 = _get_v1()
    net_v1 = _get_networking_v1()

    # 1. Create the namespace
    try:
        v1.create_namespace(
            body=client.V1Namespace(
                metadata=client.V1ObjectMeta(
                    name=ns_name,
                    labels={
                        "kubernetes.io/metadata.name": ns_name,
                        "kuboco-user-id": str(user_id),
                    },
                )
            )
        )
        logger.info("Created namespace %s", ns_name)
    except ApiException as exc:
        if exc.status != 409:
            raise

    # 2. Deny-all ingress
    try:
        net_v1.create_namespaced_network_policy(
            namespace=ns_name,
            body=client.V1NetworkPolicy(
                metadata=client.V1ObjectMeta(name="deny-all-ingress", namespace=ns_name),
                spec=client.V1NetworkPolicySpec(
                    pod_selector=client.V1LabelSelector(),
                    policy_types=["Ingress"],
                ),
            ),
        )
    except ApiException as exc:
        if exc.status != 409:
            raise

    # 3. Allow ingress from backend pods in the "kuboco" namespace
    try:
        net_v1.create_namespaced_network_policy(
            namespace=ns_name,
            body=client.V1NetworkPolicy(
                metadata=client.V1ObjectMeta(name="allow-from-backend", namespace=ns_name),
                spec=client.V1NetworkPolicySpec(
                    pod_selector=client.V1LabelSelector(
                        match_labels={"app": "kuboco-container"}
                    ),
                    policy_types=["Ingress"],
                    ingress=[
                        client.V1NetworkPolicyIngressRule(
                            _from=[
                                client.V1NetworkPolicyPeer(
                                    namespace_selector=client.V1LabelSelector(
                                        match_labels={
                                            "kubernetes.io/metadata.name": "kuboco"
                                        }
                                    ),
                                    pod_selector=client.V1LabelSelector(
                                        match_labels={"app": "kuboco-backend"}
                                    ),
                                )
                            ]
                        )
                    ],
                ),
            ),
        )
    except ApiException as exc:
        if exc.status != 409:
            raise

    return ns_name


# --------------------------------------------------------------------------- #
# Synchronous helpers (run in thread pool)
# --------------------------------------------------------------------------- #

def _sync_create(
    user_id: int,
    container_id: int,
    image: str,
) -> tuple[str, str, str]:
    """Returns (pod_name, svc_name, namespace)."""
    ns = _sync_ensure_user_namespace(user_id)
    v1 = _get_v1()
    p_name = _pod_name(user_id, container_id)
    s_name = _svc_name(user_id, container_id)

    pod = _build_pod(p_name, user_id, container_id, image, ns)
    svc = _build_service(s_name, p_name, ns)

    try:
        v1.create_namespaced_pod(namespace=ns, body=pod)
    except ApiException as exc:
        if exc.status != 409:
            raise
        # Stale orphan pod (e.g. DB was reset but K8s wasn't cleaned). Delete and recreate.
        logger.warning("Pod %s already exists (orphan); deleting and recreating", p_name)
        try:
            v1.delete_namespaced_pod(name=p_name, namespace=ns)
        except ApiException:
            pass
        v1.create_namespaced_pod(namespace=ns, body=pod)

    try:
        v1.create_namespaced_service(namespace=ns, body=svc)
    except ApiException as exc:
        if exc.status != 409:
            raise
        logger.warning("Service %s already exists (orphan); deleting and recreating", s_name)
        try:
            v1.delete_namespaced_service(name=s_name, namespace=ns)
        except ApiException:
            pass
        v1.create_namespaced_service(namespace=ns, body=svc)

    image_policy = settings.image_network_policies.get(image)
    if image_policy is not None:
        np_name = _netpol_name(user_id, container_id)
        netpol = _build_cilium_netpol(np_name, p_name, ns, image_policy)
        try:
            _get_custom_api().create_namespaced_custom_object(
                group="cilium.io",
                version="v2",
                namespace=ns,
                plural="ciliumnetworkpolicies",
                body=netpol,
            )
            logger.debug("Created CiliumNetworkPolicy %s", np_name)
        except ApiException as exc:
            logger.warning(
                "Could not create CiliumNetworkPolicy %s (Cilium available?): %s",
                np_name, exc,
            )

    return p_name, s_name, ns


def _sync_delete(user_id: int, container_id: int, namespace: str) -> None:
    v1 = _get_v1()
    p_name = _pod_name(user_id, container_id)
    s_name = _svc_name(user_id, container_id)
    np_name = _netpol_name(user_id, container_id)

    for fn, name in [
        (v1.delete_namespaced_pod, p_name),
        (v1.delete_namespaced_service, s_name),
    ]:
        try:
            fn(name=name, namespace=namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise

    try:
        _get_custom_api().delete_namespaced_custom_object(
            group="cilium.io",
            version="v2",
            namespace=namespace,
            plural="ciliumnetworkpolicies",
            name=np_name,
        )
        logger.debug("Deleted CiliumNetworkPolicy %s", np_name)
    except ApiException as exc:
        if exc.status != 404:
            logger.warning("Could not delete CiliumNetworkPolicy %s: %s", np_name, exc)


def _sync_get_status(user_id: int, container_id: int, namespace: str) -> str:
    v1 = _get_v1()
    p_name = _pod_name(user_id, container_id)

    try:
        pod = v1.read_namespaced_pod(name=p_name, namespace=namespace)
    except ApiException as exc:
        if exc.status == 404:
            return "stopped"
        raise

    phase = (pod.status.phase or "Unknown").lower()

    if phase == "running":
        statuses = pod.status.container_statuses or []
        if statuses and all(cs.ready for cs in statuses):
            return "running"
        return "starting"

    if phase in ("pending",):
        return "starting"

    if phase in ("succeeded", "failed", "unknown"):
        return "stopped"

    return "starting"


def _sync_get_pod_ip(user_id: int, container_id: int, namespace: str) -> Optional[str]:
    v1 = _get_v1()
    p_name = _pod_name(user_id, container_id)
    try:
        pod = v1.read_namespaced_pod(name=p_name, namespace=namespace)
        return pod.status.pod_ip
    except ApiException as exc:
        if exc.status == 404:
            return None
        raise


# --------------------------------------------------------------------------- #
# Async public API
# --------------------------------------------------------------------------- #

async def ensure_user_namespace(user_id: int) -> str:
    """Create the per-user namespace (idempotent). Returns namespace name."""
    return await asyncio.to_thread(_sync_ensure_user_namespace, user_id)


async def create_pod_and_service(
    user_id: int,
    container_id: int,
    image: str,
) -> tuple[str, str, str]:
    """Returns (pod_name, svc_name, namespace). Raises RuntimeError on failure."""
    return await asyncio.to_thread(_sync_create, user_id, container_id, image)


async def delete_pod_and_service(user_id: int, container_id: int, namespace: str) -> None:
    await asyncio.to_thread(_sync_delete, user_id, container_id, namespace)


async def get_pod_status(user_id: int, container_id: int, namespace: str) -> str:
    """Returns one of: pending, starting, running, stopped."""
    return await asyncio.to_thread(_sync_get_status, user_id, container_id, namespace)


async def get_pod_ip(user_id: int, container_id: int, namespace: str) -> Optional[str]:
    return await asyncio.to_thread(_sync_get_pod_ip, user_id, container_id, namespace)
