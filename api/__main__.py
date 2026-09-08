"""`python -m api` — serve the API on MPIRE_API_HOST:MPIRE_API_PORT (default 127.0.0.1:8080)."""
from __future__ import annotations

import logging

import uvicorn

from services.config import get_settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    uvicorn.run("api.app:create_app", factory=True, host=settings.api_host, port=settings.api_port,
                log_level="info", access_log=False)  # our middleware writes the access log (no query strings)


if __name__ == "__main__":
    main()
