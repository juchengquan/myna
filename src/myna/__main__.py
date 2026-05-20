from __future__ import annotations

import uvicorn

from myna.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "myna.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=settings.env == "development",
    )


if __name__ == "__main__":
    main()
