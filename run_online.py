from __future__ import annotations

import os
import socket
import threading
import webbrowser

import uvicorn

from online.app import app
from online.webv1 import register_web_v1

register_web_v1(app)


def port_is_available(host: str, port: int) -> bool:
    """Return True only when this process can bind the requested local server address."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((host, port))
        return True
    except OSError:
        return False


def main() -> int:
    host = os.environ.get("DIRECTBOOKING_HOST", "127.0.0.1")
    port = int(os.environ.get("DIRECTBOOKING_PORT", "8000"))

    if not port_is_available(host, port):
        print()
        print(f"Direct Booking is already running on http://{host}:{port}.")
        print("Close the existing Direct Booking command window before starting this build.")
        print("This build has NOT opened the older running copy in your browser.")
        return 2

    if os.environ.get("DIRECTBOOKING_NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    print(f"Direct Booking Web V1 starting at http://{host}:{port}")
    print("Press Ctrl+C in this window when you want to stop it.")
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
