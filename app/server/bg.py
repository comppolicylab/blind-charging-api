import contextlib
import logging
import threading
import time
from typing import Callable, Generator, cast

import uvicorn
from fastapi import FastAPI

logger = logging.getLogger(__name__)


AppBackgroundTask = Callable[[FastAPI], None]


# Adapted from: https://bugfactory.io/articles/starting-and-stopping-uvicorn-in-the-background/
class BackgroundServer(uvicorn.Server):
    """A uvicorn server that can be run in a background thread."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._bg_task_cv = threading.Condition()
        self._bg_tasks = list[threading.Thread]()

    @contextlib.contextmanager
    def run_in_thread(self) -> Generator:
        thread = threading.Thread(target=self.run)
        thread.start()
        try:
            while not self.started:
                time.sleep(0.001)
                pass
            for task in self._bg_tasks:
                task.start()
            yield
        finally:
            self.should_exit = True
            with self._bg_task_cv:
                self._bg_task_cv.notify_all()
            logger.info("Waiting for periodic tasks to exit ...")
            [task.join() for task in self._bg_tasks]
            logger.info("Waiting for server to exit ...")
            thread.join()
            logger.info("Bye!")

    def add_periodic_task(self, period: int, task: AppBackgroundTask) -> None:
        """Add a periodic task to the server.

        Args:
            period (int): The period in seconds.
            task (callable): The callback to run.
        """
        task_name = task.__name__

        def _run_task() -> None:
            while True:
                with self._bg_task_cv:
                    self._bg_task_cv.wait(period)
                    if self.should_exit:
                        logger.info(f"Exiting background task {task_name}")
                        return
                    task(cast(FastAPI, self.config.app))

        self._bg_tasks.append(threading.Thread(target=_run_task))
