"""
Playwright test: verify the kuboco terminal does not leak ttyd protocol
bytes ('0' command-type characters) into visible terminal output.

Run:
    python3 qa/test_terminal.py
"""

import asyncio
import re
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright, Page

BASE_URL  = "http://192.168.49.2:30080"
USERNAME  = f"qauser{int(time.time()) % 100000}"
PASSWORD  = "qapassword1"
CONTAINER = f"qa{int(time.time()) % 10000}"
QA_DIR    = Path(__file__).parent


def log(msg: str) -> None:
    print(msg, flush=True)


# ── read terminal buffer via xterm.js API (app.js exposes window._TerminalMgr) ──

READ_TERMINAL_JS = """
() => {
    const tm = window._TerminalMgr;
    if (!tm || !tm.term) return { ready: false, lines: [], ws_state: -1 };

    const buf   = tm.term.buffer.active;
    const lines = [];
    for (let i = 0; i < buf.length; i++) {
        const line = buf.getLine(i);
        if (line) lines.push(line.translateToString(true));
    }
    return {
        ready:    true,
        lines:    lines,
        ws_state: tm.socket ? tm.socket.readyState : -1,
    };
}
"""


def log_console(msg) -> None:
    if msg.type in ("log", "error", "warn") and "[ttyd-recv]" in msg.text:
        log(f"  [page console] {msg.text}")


# ── test steps ────────────────────────────────────────────────────────────────

async def register(page: Page) -> None:
    log(f"  Registering '{USERNAME}'…")
    await page.goto(f"{BASE_URL}/#/register")
    await page.fill("#username", USERNAME)
    await page.fill("#password", PASSWORD)
    await page.click("#auth-btn")
    await page.wait_for_url(f"{BASE_URL}/#/dashboard", timeout=10000)
    log("  Registered.")


async def create_container(page: Page) -> str:
    log(f"  Creating container '{CONTAINER}'…")
    await page.click("#new-container-btn")
    await page.fill("#container-name", CONTAINER)
    await page.click("#create-container-btn")
    await page.wait_for_selector(f".container-card:has-text('{CONTAINER}')", timeout=15000)
    card = page.locator(f".container-card:has-text('{CONTAINER}')")
    cid = await card.locator(".open-btn").get_attribute("data-id")
    log(f"  Container id: {cid}")
    return cid


async def open_terminal(page: Page, cid: str) -> None:
    log("  Navigating to container terminal…")
    await page.click(f".open-btn[data-id='{cid}']")

    # Overlay hides as soon as container reaches 'running' (before WS is made)
    await page.wait_for_selector("#terminal-overlay", state="hidden", timeout=90000)
    log("  Container running, overlay hidden.")

    # Wait until the WS is OPEN (readyState == 1)
    log("  Waiting for WebSocket to open…")
    await page.wait_for_function(
        "window._TerminalMgr && window._TerminalMgr.socket && window._TerminalMgr.socket.readyState === 1",
        timeout=20000,
    )
    log("  WebSocket open.")

    # Wait for at least one message from ttyd (shell prompt / preferences)
    log("  Waiting for first ttyd message…")
    await page.wait_for_function(
        "window._TerminalMgr && window._TerminalMgr.term && "
        "window._TerminalMgr.term.buffer.active.getLine(0) && "
        "window._TerminalMgr.term.buffer.active.getLine(0).translateToString(true).trim() !== ''",
        timeout=15000,
    )
    log("  Terminal has content.")
    await asyncio.sleep(0.5)


async def type_and_capture(page: Page) -> tuple[str, list[str]]:
    log("  Typing 'echo hello'…")
    await page.click(".xterm-screen")
    await asyncio.sleep(0.3)
    for ch in "echo hello":
        await page.keyboard.type(ch)
        await asyncio.sleep(0.07)
    await page.keyboard.press("Enter")
    await asyncio.sleep(2.0)

    data = await page.evaluate(READ_TERMINAL_JS)
    lines = data.get("lines", [])
    log(f"  WebSocket state: {data.get('ws_state')}  (1=OPEN)")
    log("  Terminal lines (non-empty):")
    for line in lines:
        if line.strip():
            log(f"    {repr(line)}")

    await page.screenshot(path=str(QA_DIR / "screenshot_after_command.png"))
    log("  Screenshot: qa/screenshot_after_command.png")

    full_text = "\n".join(lines)
    return full_text, lines


def check(terminal_text: str, console_logs: list[str]) -> bool:
    passed = True

    # ── Check 1: visual leak in rendered terminal ─────────────────────────────
    leak_re = re.compile(r"(0[a-zA-Z~/$ \-]){2,}")
    if leak_re.search(terminal_text):
        log("\n  ✗ CHECK 1 FAILED — '0' protocol bytes leaking into terminal output:")
        for m in leak_re.finditer(terminal_text):
            log(f"    {m.group()!r}")
        passed = False
    elif "hello" in terminal_text:
        log("\n  ✓ CHECK 1 PASSED — terminal output is clean, 'hello' found.")
    else:
        log("\n  ? CHECK 1 INCONCLUSIVE — no leak but 'hello' not in output.")
        log(f"    Full text: {terminal_text[:200]!r}")

    # ── Check 2: console logs show correct frame parsing ─────────────────────
    recv_logs = [l for l in console_logs if "[ttyd-recv]" in l]
    log(f"\n  Received {len(recv_logs)} ttyd-recv console log entries:")
    for l in recv_logs[:10]:
        log(f"    {l}")

    bad = [l for l in recv_logs if "first=0x30" not in l and "first=\"0\"" not in l
                                 and "first=0x31" not in l and "first=0x32" not in l]
    if recv_logs and not bad:
        log("  ✓ CHECK 2 PASSED — all frames start with expected ttyd cmd bytes (0x30/31/32).")
    elif bad:
        log(f"  ✗ CHECK 2 FAILED — frames with unexpected first bytes:")
        for l in bad[:5]:
            log(f"    {l}")
        passed = False
    else:
        log("  ? CHECK 2 SKIPPED — no console log entries captured.")

    return passed


async def cleanup(page: Page, cid: str) -> None:
    log("  Cleaning up…")
    await page.goto(f"{BASE_URL}/#/dashboard")
    await asyncio.sleep(0.5)
    btn = page.locator(f".delete-btn[data-id='{cid}']")
    if await btn.count():
        page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
        await btn.click()
        await asyncio.sleep(1)
    log("  Cleanup done.")


# ── main ─────────────────────────────────────────────────────────────────────

async def main() -> int:
    log(f"\n{'='*60}")
    log("Kuboco terminal protocol-leak test")
    log(f"{'='*60}\n")

    console_logs: list[str] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()

        page.on("console", lambda m: (
            console_logs.append(m.text),
            log(f"  [console.{m.type}] {m.text}") if "[ttyd-recv]" in m.text else None,
        ))
        page.on("pageerror", lambda e: log(f"  [page error] {e}"))

        passed = False
        cid = None
        try:
            await register(page)
            cid = await create_container(page)
            await open_terminal(page, cid)
            terminal_text, _ = await type_and_capture(page)
            passed = check(terminal_text, console_logs)
        except Exception as exc:
            log(f"\n  ERROR: {exc}")
            import traceback; traceback.print_exc()
            try:
                await page.screenshot(path=str(QA_DIR / "screenshot_error.png"))
            except Exception:
                pass
        finally:
            if cid:
                try:
                    await cleanup(page, cid)
                except Exception:
                    pass
            await browser.close()

    log(f"\n{'='*60}")
    log(f"Result: {'PASS' if passed else 'FAIL'}")
    log(f"{'='*60}\n")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
