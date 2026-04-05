from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Security
    secret_key: str = "INSECURE-default-change-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24  # 24 hours

    # Database
    database_url: str = "sqlite+aiosqlite:///./kokoko.db"

    # Kubernetes
    container_image: str = "kokoko/ubuntu-ttyd:latest"
    container_namespace: str = "kokoko-containers"
    backend_namespace: str = "kokoko"
    pod_cpu_limit: str = "1"
    pod_memory_limit: str = "1Gi"
    pod_cpu_request: str = "100m"
    pod_memory_request: str = "128Mi"
    ttyd_port: int = 7681
    max_containers_per_user: int = 5
    kubeconfig_path: Optional[str] = None

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
