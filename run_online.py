from __future__ import annotations

import os
import threading
import webbrowser

import uvicorn

from online.app import app
from online.setup015 import register_setup015

register_setup015(app)


def main() -> None:
    host = os.environ.get("DIRECTBOOKING_HOST", "127.0.0.1")
    port = int(os.environ.get("DIRECTBOOKING_PORT", "8000"))
    if os.environ.get("DIRECTBOOKING_NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    print(f"Direct Booking Online Build 015 starting at http://{host}:{port}")
    print("Press Ctrl+C in this window when you want to stop it.")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
