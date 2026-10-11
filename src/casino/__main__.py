from __future__ import annotations

import asyncio
import signal

from casino.config.settings import Settings
from casino.container import ContainerFactory
from casino.discord_bot.bot import CasinoBot
from casino.logging.logger import get_logger


class ApplicationRunner:
    def run(self) -> None:
        asyncio.run(self._run())

    async def _run(self) -> None:
        settings = Settings()  # type: ignore[call-arg]
        container = ContainerFactory().build(settings)
        bot = CasinoBot(container)
        logger = get_logger("casino.main")

        loop = asyncio.get_running_loop()
        stop = asyncio.Event()

        def request_stop() -> None:
            stop.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, request_stop)
            except NotImplementedError:
                signal.signal(sig, lambda _s, _f: request_stop())

        await container.startup()
        bot_task = asyncio.create_task(bot.start(settings.discord_token.get_secret_value()))
        stopper = asyncio.create_task(stop.wait())
        done, pending = await asyncio.wait({bot_task, stopper}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        logger.info("shutdown_started")
        await bot.close()
        await container.shutdown()
        if bot_task in done and bot_task.exception():
            raise bot_task.exception()


def main() -> None:
    ApplicationRunner().run()


if __name__ == "__main__":
    main()
