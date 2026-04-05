"""Kubernetes controller: create/delete/status pods and services for user containers.

All kubernetes API calls use the synchronous client wrapped in asyncio.to_thread
to avoid blocking the FastAPI event loop.
"""

import asyncio
import logging
from typing import Optional

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from backend.config import settings

logger = logging.getLogger(__name__)


def _pod_name(user_id: int, container_id: int) -> str:
    return f"kokoko-{user_id}-{container_id}"


def _svc_name(user_id: int, container_id: int) -> str:
    return f"kokoko-svc-{user_id}-{container_id}"


def get_svc_dns(user_id: int, container_id: int) -> str:
    return (
        f"kokoko-svc-{user_id}-{container_id}"
        f".{settings.container_namespace}.svc.cluster.local"
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


def _build_pod(
    pod_name: str,
    user_id: int,
    container_id: int,
    image: str,
    namespace: str,
) -> client.V1Pod:
    labels = {
        "app": "kokoko-container",
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
                "app": "kokoko-container",
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
# Synchronous helpers (run in thread pool)
# --------------------------------------------------------------------------- #

def _sync_create(
    user_id: int,
    container_id: int,
    image: str,
) -> tuple[str, str]:
    v1 = _get_v1()
    ns = settings.container_namespace
    p_name = _pod_name(user_id, container_id)
    s_name = _svc_name(user_id, container_id)

    pod = _build_pod(p_name, user_id, container_id, image, ns)
    svc = _build_service(s_name, p_name, ns)

    v1.create_namespaced_pod(namespace=ns, body=pod)
    v1.create_namespaced_service(namespace=ns, body=svc)
    return p_name, s_name


def _sync_delete(user_id: int, container_id: int) -> None:
    v1 = _get_v1()
    ns = settings.container_namespace
    p_name = _pod_name(user_id, container_id)
    s_name = _svc_name(user_id, container_id)

    for fn, name in [
        (v1.delete_namespaced_pod, p_name),
        (v1.delete_namespaced_service, s_name),
    ]:
        try:
            fn(name=name, namespace=ns)
        except ApiException as exc:
            if exc.status != 404:
                raise


def _sync_get_status(user_id: int, container_id: int) -> str:
    v1 = _get_v1()
    ns = settings.container_namespace
    p_name = _pod_name(user_id, container_id)

    try:
        pod = v1.read_namespaced_pod(name=p_name, namespace=ns)
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


def _sync_get_pod_ip(user_id: int, container_id: int) -> Optional[str]:
    v1 = _get_v1()
    ns = settings.container_namespace
    p_name = _pod_name(user_id, container_id)
    try:
        pod = v1.read_namespaced_pod(name=p_name, namespace=ns)
        return pod.status.pod_ip
    except ApiException:
        return None


# --------------------------------------------------------------------------- #
# Async public API
# --------------------------------------------------------------------------- #

async def create_pod_and_service(
    user_id: int,
    container_id: int,
    image: str,
) -> tuple[str, str]:
    """Returns (pod_name, svc_name). Raises RuntimeError on failure."""
    return await asyncio.to_thread(_sync_create, user_id, container_id, image)


async def delete_pod_and_service(user_id: int, container_id: int) -> None:
    await asyncio.to_thread(_sync_delete, user_id, container_id)


async def get_pod_status(user_id: int, container_id: int) -> str:
    """Returns one of: pending, starting, running, stopped."""
    return await asyncio.to_thread(_sync_get_status, user_id, container_id)


async def get_pod_ip(user_id: int, container_id: int) -> Optional[str]:
    return await asyncio.to_thread(_sync_get_pod_ip, user_id, container_id)
