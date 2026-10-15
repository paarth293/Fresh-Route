"""Single-process cloud entrypoint; also usable for local deployment checks."""
from __future__ import annotations

import os
from pathlib import Path
import sys


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root / "backend"))
    try:
        port = int(os.environ.get("PORT", "8000"))
    except ValueError:
        raise SystemExit("PORT must be an integer between 1 and 65535.")
    if not 1 <= port <= 65535:
        raise SystemExit("PORT must be an integer between 1 and 65535.")
    if os.environ.get("FRESHROUTE_REQUIRE_AUTH") == "1":
        if len(os.environ.get("FRESHROUTE_OPERATOR_PASSWORD", "")) < 12:
            raise SystemExit("Set FRESHROUTE_OPERATOR_PASSWORD to at least 12 characters before public deployment.")
    if not (root / "frontend/dist/index.html").is_file():
        raise SystemExit("The frontend build is missing. Build frontend/ before starting the service.")
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=port,
        workers=1,
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"),
    )


if __name__ == "__main__":
    main()
