import asyncio
import logging

from workbench.composition import tables  # noqa: F401
from workbench.modules.media.lifecycle import maintain
from workbench.modules.media.worker import process_run
from workbench.modules.runs.executor import serve


def main():
    logging.basicConfig(level=logging.INFO)
    asyncio.run(
        serve(
            "media",
            82139709,
            process_run,
            maintenance=maintain,
            maintenance_interval=2,
            maintenance_when_active=True,
        )
    )


if __name__ == "__main__":
    main()
