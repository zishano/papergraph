"""Offline browser smoke test for the local website.

Optional dependencies (outside the ordinary pytest suite):
    python -m pip install playwright
    python -m playwright install chromium
    python scripts/test_web_ui.py

PAPERGRAPH_TEST_BROWSER can select a Chromium executable. Otherwise a system
Chromium/Chrome is preferred, then Playwright's installed Chromium. All API
responses are fixtures; no credentials or external services are used. Screenshots
are written to a temporary directory and removed after the run.
"""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import tempfile
import threading
from urllib.parse import urlparse


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    try:
        from playwright.sync_api import expect, sync_playwright
    except ImportError:
        raise SystemExit("Install optional browser testing dependencies: python -m pip install playwright")

    static = Path(__file__).resolve().parents[1] / "papergraph/static"
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(static)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address = f"http://127.0.0.1:{server.server_port}"
    nodes = [
        dict(id=identifier, title=f"{identifier.upper()} Research paper", hop=hop,
             parent_ids=parents, year=2026, venue="Test conference",
             abstract="A complete abstract.", abstract_status="complete",
             publication_status="published")
        for identifier, hop, parents in [
            ("s", 0, []), ("a", 1, ["s"]), ("b", 1, ["s"]),
            ("c", 2, ["a", "b"]), ("d", 2, ["a"]), ("e", 3, ["c"]),
        ]
    ]
    result = dict(
        nodes=nodes, seed_ids=["s"],
        edges=[dict(source_id=source, target_id=target) for source, target in
               [("a", "s"), ("b", "s"), ("c", "a"), ("c", "b"), ("d", "a"), ("e", "c")]],
        paths=[], warnings=[], analysis=[], truncated=False,
    )
    job = dict(id="a" * 32, status="completed", query=dict(paper="Test Paper"),
               created="2026-11-16T10:00:00Z", logs=[])
    actions, errors = [], []

    def handle(route):
        request = route.request
        if not request.url.startswith(address + "/"):
            route.abort()  # This test must never contact external services.
            return
        path = urlparse(request.url).path
        if not path.startswith("/api/"):
            route.continue_()
            return
        if path == "/api/config":
            data = {"serpapi": True, "gemini": True}
        elif path == "/api/jobs" and request.method == "GET":
            data = [job]
        elif path == "/api/jobs" and request.method == "POST":
            actions.append(request.post_data_json)
            data = job
        elif path.endswith("/result"):
            data = result
        elif path.endswith("/resume") or path.endswith("/cancel"):
            actions.append(path)
            job["status"] = "queued" if path.endswith("/resume") else "cancelled"
            data = job
        elif path == "/api/jobs/" + job["id"]:
            data = job
        else:
            raise AssertionError(f"Unexpected API request: {request.method} {path}")
        route.fulfill(json=data)

    try:
        with tempfile.TemporaryDirectory(prefix="papergraph-ui-test-") as temporary:
            with sync_playwright() as playwright:
                executable = (os.getenv("PAPERGRAPH_TEST_BROWSER") or shutil.which("chromium")
                              or shutil.which("chromium-browser") or shutil.which("google-chrome"))
                options = {"headless": True}
                if executable:
                    options["executable_path"] = executable
                browser = playwright.chromium.launch(**options)
                try:
                    page = browser.new_page(viewport={"width": 1440, "height": 1100})
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.route("**/*", handle)
                    page.goto(address)
                    page.locator("#history button").click()
                    expect(page.locator(".graph-node")).to_have_count(6)
                    expect(page.locator(".graph-edge")).to_have_count(6)
                    page.locator('[data-node-id="a"]').click()
                    expect(page.locator("#graph-detail")).to_contain_text("A Research paper")

                    page.get_by_role("button", name="折叠后续分支").click()
                    expect(page.locator(".graph-node")).to_have_count(5)
                    # Shared descendants remain reachable through B; only D disappears.
                    expect(page.locator('[data-node-id="c"]')).to_have_count(1)
                    expect(page.locator('[data-node-id="e"]')).to_have_count(1)
                    expect(page.locator('[data-node-id="d"]')).to_have_count(0)
                    page.get_by_role("button", name="展开后续分支").click()
                    expect(page.locator(".graph-node")).to_have_count(6)
                    before = page.locator("#graph-scene").get_attribute("transform")
                    page.get_by_role("button", name="放大引用图").click()
                    assert before != page.locator("#graph-scene").get_attribute("transform")
                    page.locator("#filter").fill("A Research")
                    expect(page.locator(".paper")).to_have_count(1)
                    page.locator("#filter").fill("")

                    page.locator("#refresh").check()
                    with page.expect_request(lambda request: request.method == "POST" and
                                             urlparse(request.url).path == "/api/jobs"):
                        page.locator("#submit").click()
                    expect(page.locator(".graph-node")).to_have_count(6)
                    assert actions[0]["refresh"] is True
                    job["status"] = "interrupted"
                    page.locator("#history button").click()
                    expect(page.get_by_role("button", name="继续查询")).to_be_visible()
                    page.get_by_role("button", name="继续查询").click()
                    expect(page.get_by_role("button", name="停止任务")).to_be_visible()
                    page.get_by_role("button", name="停止任务").click()
                    expect(page.locator("#status")).to_have_text("已取消")
                    assert actions[-2:] == [f'/api/jobs/{job["id"]}/resume',
                                            f'/api/jobs/{job["id"]}/cancel']
                    job["status"] = "cancelling"
                    page.locator("#history button").click()
                    expect(page.locator("#status")).to_have_text("正在停止")
                    job["status"] = "completed"
                    page.locator("#history button").click()
                    expect(page.locator(".graph-node")).to_have_count(6)
                    page.screenshot(path=str(Path(temporary) / "desktop.png"), full_page=True)
                    page.set_viewport_size({"width": 390, "height": 844})
                    assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
                    page.screenshot(path=str(Path(temporary) / "mobile.png"), full_page=True)
                    assert not errors, errors
                finally:
                    browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    print("Passed: shared descendants, graph selection/zoom/collapse, filtering, refresh, "
          "resume/cancel states, responsive layout; no external requests or JavaScript errors.")


if __name__ == "__main__":
    main()
