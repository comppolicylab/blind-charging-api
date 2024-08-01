import contextlib
import threading
import time
from typing import Generator

import uvicorn


# Adapted from: https://bugfactory.io/articles/starting-and-stopping-uvicorn-in-the-background/
class BackgroundServer(uvicorn.Server):
    """A uvicorn server that can be run in a background thread."""

    @contextlib.contextmanager
    def run_in_thread(self) -> Generator:
        thread = threading.Thread(target=self.run)
        thread.start()
        try:
            while not self.started:
                time.sleep(0.001)
                pass
            yield
        finally:
            self.should_exit = True
            thread.join()
