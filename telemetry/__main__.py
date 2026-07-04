from __future__ import annotations

import argparse
import asyncio
import logging

from .core.registry import load_deployment
from .core.runtime import run


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a telemetry deployment.")
    parser.add_argument("deployment", help="Path to a pure-data deployment CONFIG file")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Logging verbosity",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    deployment = load_deployment(args.deployment)
    asyncio.run(run(deployment))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
