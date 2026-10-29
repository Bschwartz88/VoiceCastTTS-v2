"""A small Chrome DevTools Protocol driver for scoped headless Chrome tests.

Used by the browser tests to load the real studio page, run JavaScript in it,
handle alert() dialogs, collect console errors and take screenshots.
"""

import base64
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request

import websocket  # websocket-client, see requirements-dev.txt

BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]


def find_browser():
    override = os.environ.get("VOICECAST_BROWSER")
    if override:
        return override if os.path.exists(override) else None
    return next((path for path in BROWSER_CANDIDATES if os.path.exists(path)), None)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Browser:
    def __init__(self, executable, width=1400, height=1000):
        profile_root = os.environ.get("VOICECAST_TEST_PROFILE_ROOT")
        if profile_root:
            root = Path(profile_root).resolve()
            project_root = Path(__file__).resolve().parents[2]
            approved = project_root / '.superpowers' / 'tmp'
            if root != approved.resolve() or not root.is_relative_to(project_root):
                raise ValueError('Test profile root must be the v2 temporary folder')
            root.mkdir(parents=True, exist_ok=True)
        self.profile = tempfile.mkdtemp(prefix="voicecast_browser_", dir=profile_root)
        self.profile_parent = Path(self.profile).resolve().parent
        if profile_root and self.profile_parent != Path(profile_root).resolve():
            raise ValueError('Profile escaped the approved test folder')
        port = free_port()
        self.proc = subprocess.Popen(
            [executable, "--headless=new", f"--remote-debugging-port={port}",
             f"--user-data-dir={self.profile}", f"--window-size={width},{height}",
             "--autoplay-policy=no-user-gesture-required", "--mute-audio", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        page = None
        for _ in range(100):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json"))
                page = next(t for t in tabs if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.2)
        if page is None:
            self.close()
            raise RuntimeError("The browser did not start")

        self.ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=600, suppress_origin=True)
        self.msg_id = 0
        self.console = []  # (kind, message): kind is a console level, "exception" or "dialog"
        self.send("Runtime.enable")
        self.send("Page.enable")

    def send(self, method, **params):
        self.msg_id += 1
        my_id = self.msg_id
        self.ws.send(json.dumps({"id": my_id, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            event = msg.get("method")
            if event == "Runtime.consoleAPICalled":
                args = msg["params"]["args"]
                text = " ".join(str(a.get("value", a.get("description", ""))) for a in args)
                self.console.append((msg["params"]["type"], text))
            elif event == "Runtime.exceptionThrown":
                details = msg["params"]["exceptionDetails"]
                self.console.append(("exception", details.get("exception", {}).get("description", str(details))))
            elif event == "Page.javascriptDialogOpening":
                # Record alert()/confirm() text and accept it, so the page doesn't block.
                self.console.append(("dialog", msg["params"]["message"]))
                self.ws.send(json.dumps({"id": 0, "method": "Page.handleJavaScriptDialog", "params": {"accept": True}}))
            if msg.get("id") == my_id:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    def js(self, expression, await_promise=True):
        """Evaluates JavaScript in the page and returns the (JSON-serializable) result."""
        result = self.send("Runtime.evaluate", expression=expression, awaitPromise=await_promise, returnByValue=True)
        if "exceptionDetails" in result:
            details = result["exceptionDetails"]
            raise RuntimeError(details.get("exception", {}).get("description", details))
        return result["result"].get("value")

    def goto(self, url):
        # Explicit fixture credentials: the server and test driver share this process.
        # Unauthenticated/cross-origin checks use raw HTTP or Page.navigate directly.
        import session_access
        import urllib.parse
        parsed = urllib.parse.urlsplit(url)
        if parsed.hostname == '127.0.0.1' and parsed.port in session_access._sessions and not parsed.fragment:
            self.send('Network.setCookie', name=f'voicecast_v2_{parsed.port}', value=session_access._sessions[parsed.port], url=f'http://127.0.0.1:{parsed.port}/', httpOnly=True, sameSite='Strict', path='/')
        # readyState alone can still describe the old document after navigate.
        # Wait for a new document before allowing tests to interact with it.
        marker = str(time.monotonic_ns())
        self.js(f"window.__cdpNavigationMarker = {json.dumps(marker)}")
        result = self.send("Page.navigate", url=url)
        if result.get("errorText"):
            raise RuntimeError(f"Navigation failed: {result['errorText']}")
        if result.get("loaderId"):
            self.wait_for(f"window.__cdpNavigationMarker !== {json.dumps(marker)} && "
                          "document.readyState === 'complete'")
        else:
            self.wait_for("document.readyState === 'complete'")

    def wait_for(self, condition, timeout=300, interval=0.25):
        end = time.time() + timeout
        while time.time() < end:
            if self.js(f"!!({condition})", await_promise=False):
                return
            time.sleep(interval)
        raise TimeoutError(f"Timed out waiting for: {condition}")

    def messages(self, kind):
        return [text for k, text in self.console if k == kind]

    def screenshot(self, path, full_page=False):
        params = {"format": "png"}
        if full_page:
            params["captureBeyondViewport"] = True
        data = self.send("Page.captureScreenshot", **params)["data"]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))

    def close(self):
        try:
            if getattr(self, "ws", None):
                self.ws.close()
        finally:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            profile = Path(self.profile).resolve()
            if profile.parent != self.profile_parent or not profile.name.startswith('voicecast_browser_'):
                raise ValueError('Refusing to remove an unexpected browser profile path')
            shutil.rmtree(profile, ignore_errors=True)
