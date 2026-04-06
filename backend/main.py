"""Kuboco — Kubernetes Container Runner Backend."""

import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import (
    create_access_token,
    decode_token,
    get_current_user,
    hash_password,
    require_owned_container,
    verify_password,
)
from backend.config import settings
from backend.database import get_db, init_db
from backend.models import Container, User

logger = logging.getLogger(__name__)

_CONTAINER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,30}[a-z0-9]$")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("Database initialized")
    yield


app = FastAPI(title="Kuboco Container Runner", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend static files. Must be mounted last (catch-all).
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")


# --------------------------------------------------------------------------- #
# Pydantic schemas
# --------------------------------------------------------------------------- #

class RegisterRequest(BaseModel):
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def username_valid(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_]{3,32}$", v):
            raise ValueError("Username must be 3-32 alphanumeric/underscore characters")
        return v

    @field_validator("password")
    @classmethod
    def password_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class LoginRequest(BaseModel):
    username: str
    password: str


class CreateContainerRequest(BaseModel):
    name: str
    image: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_valid(cls, v: str) -> str:
        v = v.lower().strip()
        if not re.match(r"^[a-z0-9][a-z0-9\-]{0,30}[a-z0-9]?$", v):
            raise ValueError(
                "Name must be 2-32 lowercase alphanumeric characters or hyphens"
            )
        return v


def _container_dict(c: Container) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "pod_name": c.pod_name,
        "svc_name": c.svc_name,
        "namespace": c.namespace,
        "status": c.status,
        "image": c.image,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "stopped_at": c.stopped_at.isoformat() if c.stopped_at else None,
    }


# --------------------------------------------------------------------------- #
# Health check (no auth required — used by k8s probes)
# --------------------------------------------------------------------------- #

@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# Images
# --------------------------------------------------------------------------- #

@app.get("/api/images")
async def list_images(current_user: User = Depends(get_current_user)):
    """Return the allowed container images with a human-readable label."""
    def _label(img: str) -> str:
        # "kuboco/ubuntu-ttyd:latest" → "ubuntu-ttyd"
        return img.split("/")[-1].split(":")[0]

    return [
        {"image": img, "label": _label(img), "default": img == settings.container_image}
        for img in settings.allowed_images
    ]


# --------------------------------------------------------------------------- #
# Auth routes
# --------------------------------------------------------------------------- #

@app.post("/api/auth/register", status_code=status.HTTP_201_CREATED)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == req.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username already taken")

    user = User(username=req.username, hashed_password=hash_password(req.password))
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = create_access_token(user.id)
    response = JSONResponse(
        {"id": user.id, "username": user.username, "access_token": token},
        status_code=201,
    )
    response.set_cookie(
        key="kuboco_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=settings.access_token_expire_minutes * 60,
    )
    return response


@app.post("/api/auth/login")
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == req.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    token = create_access_token(user.id)
    response = JSONResponse({"id": user.id, "username": user.username, "access_token": token})
    response.set_cookie(
        key="kuboco_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=settings.access_token_expire_minutes * 60,
    )
    return response


@app.post("/api/auth/logout")
async def logout():
    response = JSONResponse({"message": "Logged out"})
    response.delete_cookie("kuboco_token")
    return response


@app.get("/api/auth/me")
async def me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "username": current_user.username,
        "created_at": current_user.created_at.isoformat() if current_user.created_at else None,
    }


# --------------------------------------------------------------------------- #
# Container routes
# --------------------------------------------------------------------------- #

@app.get("/api/containers")
async def list_containers(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Container)
        .where(Container.user_id == current_user.id)
        .order_by(Container.created_at.desc())
    )
    containers = result.scalars().all()
    return [_container_dict(c) for c in containers]


@app.post("/api/containers", status_code=status.HTTP_201_CREATED)
async def create_container(
    req: CreateContainerRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Enforce per-user limit
    count_result = await db.execute(
        select(func.count())
        .select_from(Container)
        .where(
            and_(
                Container.user_id == current_user.id,
                Container.status.notin_(["stopped"]),
            )
        )
    )
    active_count = count_result.scalar_one()
    if active_count >= settings.max_containers_per_user:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {settings.max_containers_per_user} active containers per user",
        )

    image = req.image or settings.container_image
    if image not in settings.allowed_images:
        raise HTTPException(
            status_code=400,
            detail=f"Image not permitted. Allowed: {', '.join(settings.allowed_images)}",
        )
    container = Container(
        user_id=current_user.id,
        name=req.name,
        pod_name="",
        svc_name="",
        namespace="",
        status="pending",
        image=image,
    )
    db.add(container)
    await db.flush()  # get container.id before k8s call

    from backend import k8s_controller as k8s

    try:
        pod_name, svc_name, namespace = await k8s.create_pod_and_service(
            user_id=current_user.id,
            container_id=container.id,
            image=image,
        )
        container.pod_name = pod_name
        container.svc_name = svc_name
        container.namespace = namespace
        container.status = "starting"
    except Exception as exc:
        container.status = "error"
        await db.commit()
        logger.error("Failed to create k8s resources for container %d: %s", container.id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to create container: {exc}")

    await db.commit()
    await db.refresh(container)
    return _container_dict(container)


@app.get("/api/containers/{container_id}")
async def get_container(
    container_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    container = await require_owned_container(container_id, current_user, db)

    # Refresh live status from k8s for non-terminal states
    if container.status not in ("stopped", "error"):
        from backend import k8s_controller as k8s

        try:
            live = await k8s.get_pod_status(current_user.id, container.id, container.namespace)
            if live != container.status:
                container.status = live
                if live == "stopped":
                    container.stopped_at = datetime.now(timezone.utc)
                await db.commit()
        except Exception as exc:
            logger.warning("Could not refresh k8s status for container %d: %s", container.id, exc)

    return _container_dict(container)


@app.delete("/api/containers/{container_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_container(
    container_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    container = await require_owned_container(container_id, current_user, db)

    if container.status != "stopped":
        from backend import k8s_controller as k8s

        try:
            await k8s.delete_pod_and_service(current_user.id, container.id, container.namespace)
        except Exception as exc:
            logger.error("Failed to delete k8s resources for container %d: %s", container.id, exc)
            raise HTTPException(status_code=500, detail=f"Failed to delete container: {exc}")

    container.status = "stopped"
    container.stopped_at = datetime.now(timezone.utc)
    await db.commit()


# --------------------------------------------------------------------------- #
# WebSocket: terminal proxy
# --------------------------------------------------------------------------- #

@app.websocket("/api/ws/terminal/{container_id}")
async def ws_terminal(
    websocket: WebSocket,
    container_id: int,
    token: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    # Authenticate via query param token
    if not token:
        await websocket.close(code=4001, reason="Missing token")
        return

    try:
        payload = decode_token(token)
        user_id = int(payload["sub"])
    except Exception:
        await websocket.close(code=4001, reason="Invalid token")
        return

    result = await db.execute(select(User).where(User.id == user_id, User.is_active == True))
    user = result.scalar_one_or_none()
    if not user:
        await websocket.close(code=4001, reason="User not found")
        return

    container = await require_owned_container(container_id, user, db)
    if container.status not in ("running", "starting"):
        await websocket.close(code=4004, reason="Container not running")
        return

    from backend.proxy import proxy_terminal_websocket

    await proxy_terminal_websocket(websocket, container)


# --------------------------------------------------------------------------- #
# WebSocket: port proxy
# --------------------------------------------------------------------------- #

@app.websocket("/api/ws/port/{container_id}/{port}/{path:path}")
async def ws_port(
    websocket: WebSocket,
    container_id: int,
    port: int,
    path: str = "",
    token: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    if not (1 <= port <= 65535):
        await websocket.close(code=4000, reason="Invalid port")
        return

    if not token:
        await websocket.close(code=4001, reason="Missing token")
        return

    try:
        payload = decode_token(token)
        user_id = int(payload["sub"])
    except Exception:
        await websocket.close(code=4001, reason="Invalid token")
        return

    result = await db.execute(select(User).where(User.id == user_id, User.is_active == True))
    user = result.scalar_one_or_none()
    if not user:
        await websocket.close(code=4001, reason="User not found")
        return

    container = await require_owned_container(container_id, user, db)
    if container.status != "running":
        await websocket.close(code=4004, reason="Container not running")
        return

    from backend.proxy import proxy_port_websocket

    await proxy_port_websocket(websocket, container, port, path)


# --------------------------------------------------------------------------- #
# HTTP: port proxy
# --------------------------------------------------------------------------- #

@app.api_route(
    "/api/proxy/{container_id}/{port}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
)
async def http_proxy(
    request: Request,
    container_id: int,
    port: int,
    path: str = "",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not (1 <= port <= 65535):
        raise HTTPException(status_code=400, detail="Invalid port number")

    container = await require_owned_container(container_id, current_user, db)
    if container.status != "running":
        raise HTTPException(status_code=400, detail="Container is not running")

    from backend.proxy import proxy_http_request

    return await proxy_http_request(request, container, port, path)


# --------------------------------------------------------------------------- #
# Serve frontend (mount last — catch-all)
# --------------------------------------------------------------------------- #

if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
