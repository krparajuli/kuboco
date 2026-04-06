from typing import Optional
from pydantic import BaseModel
from pydantic_settings import BaseSettings


class ImageNetworkPolicy(BaseModel):
    """Egress rules for pods created from a specific image.

    Implemented via CiliumNetworkPolicy (requires Cilium CNI).
    Ingress is controlled at the namespace level (deny-all-ingress +
    allow-from-backend standard NetworkPolicies), so only egress is configured here.

    egress_deny_fqdns: domains blocked on egress (wildcard patterns supported, e.g. *.google.com).
    """
    egress_deny_fqdns: list[str] = []


class Settings(BaseSettings):
    # Security
    secret_key: str = "INSECURE-default-change-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24  # 24 hours

    # Database
    database_url: str = "sqlite+aiosqlite:///./kuboco.db"

    # Kubernetes
    container_image: str = "kuboco/ubuntu-ttyd:latest"
    allowed_images: list[str] = [
        "kuboco/ubuntu-ttyd:latest",
        "kuboco/ironclaude:latest",
    ]
    container_namespace: str = "kuboco-containers"
    backend_namespace: str = "kuboco"
    pod_cpu_limit: str = "1"
    pod_memory_limit: str = "1Gi"
    pod_cpu_request: str = "100m"
    pod_memory_request: str = "128Mi"
    ttyd_port: int = 7681
    max_containers_per_user: int = 5
    kubeconfig_path: Optional[str] = None

    # Per-image network policies (applied as CiliumNetworkPolicy).
    # Keys must match entries in allowed_images.
    image_network_policies: dict[str, ImageNetworkPolicy] = {
        "kuboco/ubuntu-ttyd:latest": ImageNetworkPolicy(
            egress_deny_fqdns=["google.com", "*.google.com"],
        ),
        "kuboco/ironclaude:latest": ImageNetworkPolicy(
            egress_deny_fqdns=["google.com", "*.google.com"],
        ),
    }

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
