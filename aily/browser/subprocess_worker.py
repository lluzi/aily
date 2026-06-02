from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from multiprocessing.connection import Listener

logger = logging.getLogger(__name__)


async def _fetch(url: str, timeout: int, profile_dir: str) -> str:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=True,
        )
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
            text = await page.inner_text("body")
            return text or ""
        finally:
            try:
                await page.close()
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass


def _run_loop(listener, fetch_fn):
    while True:
        with listener.accept() as conn:
            while True:
                try:
                    msg = conn.recv()
                except EOFError:
                    break
                if msg.get("type") == "shutdown":
                    conn.send({"status": "ok"})
                    return
                if msg.get("type") == "fetch":
                    url = msg.get("url")
                    timeout = msg.get("timeout", 60)
                    try:
                        text = fetch_fn(url, timeout)
                        conn.send({"status": "ok", "text": text})
                    except Exception as exc:
                        logger.exception("Fetch failed for %s", url)
                        conn.send({"status": "error", "message": str(exc)})
                else:
                    conn.send({"status": "error", "message": "Unknown type"})


def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-dir", required=True)
    parser.add_argument("--authkey", default="aily-browser")
    args = parser.parse_args(argv)

    authkey = args.authkey.encode()
    profile_dir = args.profile_dir

    with Listener(("localhost", 0), authkey=authkey) as listener:
        print(f"READY {listener.address[1]}", flush=True)
        _run_loop(
            listener,
            lambda url, timeout: asyncio.run(_fetch(url, timeout, profile_dir)),
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
