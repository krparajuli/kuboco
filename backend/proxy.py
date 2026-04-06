"""WebSocket and HTTP proxy logic for forwarding traffic to user pods."""

import asyncio
import logging
import re
from typing import Optional

import httpx
import websockets
import websockets.exceptions
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from starlette.requests import Request

from backend.config import settings
from backend.models import Container

logger = logging.getLogger(__name__)

_BASE_TAG_RE = re.compile(rb"(<head(?:\s[^>]*)?>)", re.IGNORECASE)


def _inject_base_tag(html_bytes: bytes, container_id: int, port: int) -> bytes:
    """Insert <base href="/api/proxy/{id}/{port}/"> immediately after <head>."""
    base_tag = f'<base href="/api/proxy/{container_id}/{port}/">'.encode()
    match = _BASE_TAG_RE.search(html_bytes)
    if match:
        pos = match.end()
        return html_bytes[:pos] + base_tag + html_bytes[pos:]
    # No <head> tag — prepend
    return base_tag + html_bytes


# --------------------------------------------------------------------------- #
# WebSocket relay helpers
# --------------------------------------------------------------------------- #

async def _browser_to_pod(browser_ws: WebSocket, pod_ws) -> None:
    """Forward messages from browser WebSocket to pod WebSocket."""
    try:
        while True:
            msg = await browser_ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                await pod_ws.send(msg["bytes"])
            elif msg.get("text") is not None:
                await pod_ws.send(msg["text"])
    except (WebSocketDisconnect, Exception):
        pass


async def _pod_to_browser(pod_ws, browser_ws: WebSocket) -> None:
    """Forward messages from pod WebSocket to browser WebSocket."""
    try:
        async for message in pod_ws:
            if isinstance(message, bytes):
                await browser_ws.send_bytes(message)
            else:
                await browser_ws.send_text(message)
    except (WebSocketDisconnect, websockets.exceptions.ConnectionClosed, Exception):
        pass


async def _relay(
    browser_ws: WebSocket,
    pod_ws,
    accept_subprotocol: Optional[str] = None,
) -> None:
    """Accept browser WS, connect to pod WS, relay both directions."""
    await browser_ws.accept(subprotocol=accept_subprotocol)
    t1 = asyncio.create_task(_browser_to_pod(browser_ws, pod_ws))
    t2 = asyncio.create_task(_pod_to_browser(pod_ws, browser_ws))
    done, pending = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


# --------------------------------------------------------------------------- #
# Public proxy functions
# --------------------------------------------------------------------------- #

async def proxy_terminal_websocket(
    browser_ws: WebSocket,
    container: Container,
) -> None:
    """Proxy browser ↔ pod ttyd WebSocket (subprotocol: tty)."""
    from backend.k8s_controller import get_svc_dns

    svc_dns = get_svc_dns(container.user_id, container.id, container.namespace)
    target_url = f"ws://{svc_dns}:{settings.ttyd_port}/ws"

    try:
        async with websockets.connect(
            target_url,
            subprotocols=["tty"],
            ping_interval=20,
            ping_timeout=10,
            open_timeout=10,
        ) as pod_ws:
            await _relay(browser_ws, pod_ws, accept_subprotocol="tty")
    except (websockets.exceptions.WebSocketException, OSError, TimeoutError) as exc:
        logger.warning("Terminal WS proxy error for container %d: %s", container.id, exc)
        try:
            await browser_ws.close(code=1011, reason="Pod connection failed")
        except Exception:
            pass


async def proxy_port_websocket(
    browser_ws: WebSocket,
    container: Container,
    port: int,
    path: str,
) -> None:
    """Proxy browser ↔ pod WebSocket on an arbitrary port."""
    from backend.k8s_controller import get_pod_ip

    pod_ip = await get_pod_ip(container.user_id, container.id, container.namespace)
    if not pod_ip:
        await browser_ws.close(code=1011, reason="Pod IP not available")
        return

    clean_path = path.lstrip("/")
    target_url = f"ws://{pod_ip}:{port}/{clean_path}"

    # Forward whatever subprotocols the browser requested
    requested_subprotocols = browser_ws.headers.get("sec-websocket-protocol", "")
    subprotocols = (
        [s.strip() for s in requested_subprotocols.split(",")]
        if requested_subprotocols
        else None
    )
    accept_subprotocol = subprotocols[0] if subprotocols else None

    try:
        connect_kwargs = {"ping_interval": 20, "ping_timeout": 10, "open_timeout": 10}
        if subprotocols:
            connect_kwargs["subprotocols"] = subprotocols

        async with websockets.connect(target_url, **connect_kwargs) as pod_ws:
            # Use the negotiated subprotocol from the upstream connection
            negotiated = (
                pod_ws.subprotocol if hasattr(pod_ws, "subprotocol") else accept_subprotocol
            )
            await _relay(browser_ws, pod_ws, accept_subprotocol=negotiated)
    except (websockets.exceptions.WebSocketException, OSError, TimeoutError) as exc:
        logger.warning(
            "Port WS proxy error for container %d port %d: %s", container.id, port, exc
        )
        try:
            await browser_ws.close(code=1011, reason="Pod connection failed")
        except Exception:
            pass


async def proxy_http_request(
    request: Request,
    container: Container,
    port: int,
    path: str,
) -> Response:
    """Forward an HTTP request to a port on the user's pod."""
    from backend.k8s_controller import get_pod_ip

    pod_ip = await get_pod_ip(container.user_id, container.id, container.namespace)
    if not pod_ip:
        return Response(
            content=b"<html><body><h2>Pod not available</h2>"
                    b"<p>Could not resolve pod IP address.</p></body></html>",
            status_code=502,
            media_type="text/html",
        )

    clean_path = path.lstrip("/")
    target_url = f"http://{pod_ip}:{port}/{clean_path}"
    if request.url.query:
        target_url += f"?{request.url.query}"

    # Forward headers, stripping hop-by-hop
    _HOP_BY_HOP = {"host", "connection", "transfer-encoding", "te", "trailer", "upgrade"}
    fwd_headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP
    }
    fwd_headers["host"] = f"{pod_ip}:{port}"
    fwd_headers["x-forwarded-for"] = request.client.host if request.client else "unknown"
    fwd_headers["x-forwarded-host"] = request.headers.get("host", "")

    body = await request.body()

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as http:
            resp = await http.request(
                method=request.method,
                url=target_url,
                headers=fwd_headers,
                content=body or None,
            )
    except httpx.ConnectError:
        return Response(
            content=b"<html><body><h2>Connection refused</h2>"
                    b"<p>The process on this port is not accepting connections yet.</p></body></html>",
            status_code=502,
            media_type="text/html",
        )
    except httpx.TimeoutException:
        return Response(content=b"Gateway timeout", status_code=504)

    # Build response headers, stripping hop-by-hop + content headers we may rewrite
    _STRIP_RESP = {"transfer-encoding", "content-encoding", "content-length"}
    resp_headers = {
        k: v
        for k, v in resp.headers.items()
        if k.lower() not in _STRIP_RESP
    }

    content = resp.content
    content_type = resp.headers.get("content-type", "")

    if "text/html" in content_type:
        content = _inject_base_tag(content, container.id, port)
        resp_headers["content-type"] = "text/html; charset=utf-8"

    return Response(
        content=content,
        status_code=resp.status_code,
        headers=resp_headers,
    )
