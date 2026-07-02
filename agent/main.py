from __future__ import annotations

import argparse
import logging

import uvicorn

from agent.settings import get_settings
from agent.web.routes import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Automatic Inspection Agent")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    args = parser.parse_args()

    if args.config:
        from pathlib import Path

        from agent.settings import Settings, reset_settings
        import agent.settings as settings_module

        reset_settings()
        settings_module._settings = Settings(Path(args.config))

    settings = get_settings()
    host = args.host or settings.config.web.host
    port = args.port or settings.config.web.port
    app = create_app()
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
