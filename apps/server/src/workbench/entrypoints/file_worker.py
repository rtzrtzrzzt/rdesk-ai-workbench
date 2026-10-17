import asyncio
import logging

from workbench.composition import tables  # noqa: F401
from workbench.modules.files.service import clean_uploads
from workbench.modules.files.worker import process_run
from workbench.modules.runs.executor import serve


def main():
    logging.basicConfig(level=logging.INFO)

    async def maintain():
        await asyncio.to_thread(clean_uploads)

    asyncio.run(serve("files", 82139704, process_run, maintenance=maintain))


if __name__ == "__main__":
    main()
