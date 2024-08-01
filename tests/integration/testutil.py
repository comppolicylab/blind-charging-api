import asyncio
import json
import logging
import os
import pathlib
import socket
import time
from threading import Condition

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse

from app.server.bg import BackgroundServer

SAMPLE_DATA_DIR = (
    pathlib.Path(os.path.dirname(__file__)).parent.parent
    / "app"
    / "server"
    / "sample_data"
)


class ExpectedRequest:
    def __init__(
        self,
        path: str,
        method: str,
        headers: dict | None = None,
        body: bytes | None = None,
        json_body: dict | None = None,
    ):
        self.path = path
        self.method = method
        self.headers = headers
        self.body = body
        self.json_body = json_body

    def __repr__(self) -> str:
        return (
            f"ExpectedRequest(path={self.path}, "
            f"method={self.method}, "
            f"headers={self.headers}, "
            f"json_body={self.json_body})"
        )

    def __str__(self) -> str:
        return self.__repr__()


class ObservedRequest:
    def __init__(self, path: str, *, method: str, headers: dict, body: bytes):
        self.path = path
        self.method = method
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.body = body

    def __eq__(self, expected: object):  # noqa: C901
        """The ExpectedRequest can specify partial information to match."""
        if not isinstance(expected, ExpectedRequest):
            return False

        if self.path != expected.path:
            return False

        if self.method.lower() != expected.method.lower():
            return False

        if expected.headers:
            for key, value in expected.headers.items():
                if self.headers.get(key.lower()) != value:
                    return False

        if expected.json_body:
            if json.loads(self.body) != expected.json_body:
                return False
        elif expected.body:
            if self.body != expected.body:
                return False

        return True

    def __repr__(self) -> str:
        return (
            f"ObservedRequest(path={self.path}, "
            f"method={self.method}, "
            f"headers={self.headers}, "
            f"body={self.body.decode()})"
        )

    def __str__(self) -> str:
        return self.__repr__()


class TestCallbackServer(BackgroundServer):
    def __init__(self, logger: logging.Logger | None = None):
        self._requests = list[ObservedRequest]()
        self._condition = Condition()
        self._logger = logger
        app = FastAPI()

        # Add a handler to return a test PDF for redaction
        app.get("/test_document.pdf")(self._return_test_document)
        # Add a handler for any fallback request
        app.route(
            "/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"]
        )(self._track_request)

        self._port = self._find_free_port()
        self._host = "127.0.0.1"
        cfg = uvicorn.Config(app, host=self._host, port=self._port)
        super().__init__(cfg)

    def _find_free_port(self, above: int = 10_000) -> int:
        """Find an arbitrary free port above a certain number.

        Args:
            above (int) - port to start looking at

        Returns:
            int - the first free port

        Raises:
            RuntimeError - if no free port is found
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        port = above
        while port < 100_000:
            try:
                sock.bind(("", port))
                sock.close()
                return port
            except OSError:
                port += 1
        raise RuntimeError("Could not find a free port")

    @property
    def base_url(self) -> str:
        """Get the base URL for the server."""
        return f"http://{self._host}:{self._port}"

    @property
    def docker_base_url(self) -> str:
        """Get the base URL for the server when running in a docker container."""
        return f"http://host.docker.internal:{self._port}"

    def _return_test_document(self):
        # Return a pdf from the `sample_data` directory, relative to this file
        fn = "P441852-response-documents.pdf"

        async def stream_file():
            with open(SAMPLE_DATA_DIR / fn, "rb") as f:
                while True:
                    chunk = f.read(1024)
                    if not chunk:
                        break
                    yield chunk
                    await asyncio.sleep(0.01)

        return StreamingResponse(stream_file(), media_type="application/pdf")

    async def _track_request(self, request: Request):
        with self._condition:
            self._requests.append(
                ObservedRequest(
                    request.url.path,
                    method=request.method,
                    headers=dict(request.headers),
                    body=await request.body(),
                )
            )
            self._condition.notify_all()
        return Response()

    def wait_for_request(
        self,
        path: str,
        method: str = "GET",
        headers: dict | None = None,
        body: bytes | None = None,
        json_body: dict | None = None,
        timeout: int = 5,
    ):
        with self._condition:
            start = time.monotonic()
            expectation = ExpectedRequest(
                path, method=method, headers=headers, body=body, json_body=json_body
            )
            while expectation not in self._requests:
                if (time.monotonic() - start) > timeout:
                    if self._logger:
                        self._logger.error("Request not received:")
                        self._logger.error(expectation)
                        if self._requests:
                            self._logger.error("Other requests were:")
                            for req in self._requests:
                                self._logger.error(req)
                        else:
                            self._logger.error("No requests were received")

                    raise TimeoutError("Request not received")
                self._condition.wait(timeout=timeout)
            self._remove_observed_request(expectation)

    def _remove_observed_request(self, expectation: ExpectedRequest):
        with self._condition:
            for i, req in enumerate(self._requests):
                if req == expectation:
                    self._requests.pop(i)
                    return
