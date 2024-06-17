import asyncio
import base64
import io
import logging
import time
from abc import ABC, abstractmethod
from typing import Type, cast

import aiohttp
from blind_charging_core import Pipeline, PipelineConfig
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils import UUID

from .config import config
from .db import Callback, Job, Redaction, Task
from .generated.models import (
    Document,
    DocumentContent,
    DocumentLink,
    RedactionResult,
    RedactionResultSuccess,
)

logger = logging.getLogger(__name__)


class Processor(ABC):
    """A background task worker."""

    _conditions: dict[str, asyncio.Condition] = {}

    def __init__(self):
        super().__init__()
        cv = asyncio.Condition(lock=asyncio.Lock())
        self._stopped = False
        self._final = False
        self._conditions[self.__class__.__name__] = cv

    def _cv(self, processor: str | Type | None = None):
        """Get the condition variable for the processor."""
        processor_name = self.__class__.__name__
        if processor:
            processor_name = (
                processor.__name__ if isinstance(processor, type) else processor
            )
        return self._conditions[processor_name]

    async def __aenter__(self):
        asyncio.create_task(self.run())
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.stop()
        await self.join()

    async def join(self, timeout: float | None = None):
        """Wait for the worker to stop."""
        t0 = time.monotonic()

        while True:
            if self._final:
                self._log(logging.INFO, "joined.")
                break
            if timeout is not None and time.monotonic() - t0 > timeout:
                self._log(logging.WARNING, "join timed out.")
                break
            await asyncio.sleep(0.1)

    async def stop(self):
        """Stop the worker."""
        self._log(logging.INFO, "Stopping ...")
        cv = self._cv()
        async with cv:
            self._stopped = True
            self._log(logging.DEBUG, "broadcasting stop signal to workers")
            cv.notify_all()

    def _log(self, level: int, msg: str, *args, **kwargs):
        logger.log(level, f"{self.__class__.__name__}: {msg}", *args, **kwargs)

    async def check(self, processor: str | Type | None = None):
        """Wake the worker to check for new work.

        Args:
            processor (str | Type, optional): The processor to wake up.
            By default it will wake the processor that this is invoked on.
        """
        cv = self._cv(processor)
        async with cv:
            cv.notify()

    async def run(self):
        """Run the processor."""
        self._log(logging.DEBUG, "Started")

        while True:
            try:
                cv = self._cv()
                async with cv:
                    if self._stopped:
                        self._log(logging.DEBUG, "received stop signal.")
                        self._final = True
                        break

                    exec_id = await self.check_for_work()
                    if exec_id:
                        self._log(logging.DEBUG, f"executor is ready: {exec_id.hex()}")
                        await self.execute(exec_id)
                    else:
                        self._log(logging.DEBUG, "no work found, going to sleep.")
                        await cv.wait()
            except Exception as e:
                self._log(logging.ERROR, "error processing work.")
                logger.exception(e)
                self._log(logging.DEBUG, "sleeping for 5 seconds before retrying ...")
                await asyncio.sleep(5)
        self._log(logging.DEBUG, "stopped.")

    @abstractmethod
    async def next(self, session: AsyncSession) -> Task | None:
        """Check for the next task to fulfill."""
        ...

    @abstractmethod
    async def create(self, session: AsyncSession, task_id: UUID) -> UUID | None:
        """Create a new task executor."""
        ...

    @abstractmethod
    async def execute(self, executor_id: UUID) -> None:
        """Handle a task through the executor."""
        ...

    async def check_for_work(self) -> UUID | None:
        """Check for a new task to fulfill."""
        new_exec_id: UUID | None = None
        task_id: UUID | None = None
        try:
            self._log(logging.DEBUG, "checking for work ...")
            async with config.db.driver.async_session() as session:
                # Get the next task to process
                try:
                    task = await self.next(session)
                    if not task:
                        self._log(logging.DEBUG, "no pending tasks found")
                        return None

                    task_id = task.id
                    self._log(logging.DEBUG, f"claiming task: {task_id.hex()}")
                    await task.claim(session)
                    await session.commit()
                except Exception as e:
                    self._log(logging.ERROR, f"error claiming task: {str(e)}")
                    logger.exception(e)
                    await session.rollback()
                    return None

            if not task_id:
                self._log(logging.WARNING, "no task ID found.")
                return None

            async with config.db.driver.async_session() as session:
                try:
                    # Kick off a new job to process the task
                    self._log(
                        logging.DEBUG, f"creating new job for task: {task_id.hex()}"
                    )
                    new_exec_id = await self.create(session, task_id)
                    await session.commit()
                except Exception as e:
                    self._log(logging.ERROR, "error creating executor.")
                    logger.exception(e)
                    await session.rollback()

            if new_exec_id:
                self._log(logging.DEBUG, f"new executor created: {new_exec_id.hex()}")

        except Exception as e:
            self._log(logging.ERROR, "Error checking for new tasks.")
            logger.exception(e)

        return new_exec_id


class CallbackProcessor(Processor):
    async def next(self, session: AsyncSession) -> Task | None:
        """Find the next task to process."""
        return await Task.next_callback_task(session)

    async def create(self, session: AsyncSession, task_id: UUID) -> UUID | None:
        """Create a new job."""
        cb = Callback(task_id=task_id)
        session.add(cb)
        await session.flush()
        return cb.id

    async def execute(self, cb_id: UUID):
        self._log(logging.DEBUG, f"processing callback: {cb_id.hex()}")
        try:
            async with config.db.driver.async_session() as session:
                try:
                    cb = await Callback.get_by_id(session, cb_id)
                    if not cb:
                        self._log(logging.ERROR, f"callback not found: {cb_id.hex()}")
                        return
                    if not cb.task:
                        self._log(logging.ERROR, f"task not found: {cb.task_id.hex()}")
                        return
                    if not cb.task.callback_url:
                        self._log(
                            logging.WARNING,
                            f"callback URL not found: {cb.task.id.hex()}",
                        )
                        await Callback.success(
                            session, cb_id, 0, "Callback URL not found."
                        )
                    else:
                        redaction = await cb.task.get_redaction(session)
                        doc = await self.format_redaction(redaction)

                        # POST the redacted document to the callback URL and record
                        # the result. Any 2xx status is a success, otherwise it's
                        # an error (including 3xx).
                        timeout = aiohttp.ClientTimeout(
                            total=config.task.callback_timeout_seconds
                        )
                        async with aiohttp.ClientSession(timeout=timeout) as client:
                            try:
                                async with client.post(
                                    cb.task.callback_url, json=doc.model_dump()
                                ) as resp:
                                    await Callback.complete(
                                        session, cb_id, resp.status, resp.reason
                                    )
                            except Exception as e:
                                self._log(
                                    logging.ERROR,
                                    "error posting to callback URL: "
                                    f"{cb.task.callback_url}",
                                )
                                logger.exception(e)
                                await Callback.complete(session, cb_id, 0, str(e))

                    await session.commit()
                except Exception as e:
                    await session.rollback()
                    raise e

        except Exception as e:
            logger.error("Error processing job.")
            logger.exception(e)
            async with config.db.driver.async_session() as session:
                await Callback.complete(session, cb_id, 0, str(e))
                await session.commit()

    async def format_redaction(self, redaction: Redaction) -> RedactionResult:
        """Format a redaction for the callback."""
        doc_root = await self.format_doc_root(redaction)
        root = RedactionResultSuccess(
            jurisdictionId=redaction.file.jurisdiction_id,
            caseId=redaction.file.case_id,
            maskedAccuseds=[],  # TODO
            redactedDocument=Document(root=doc_root),
            status="COMPLETE",
        )
        return RedactionResult(root=root)

    async def format_doc_root(
        self, redaction: Redaction
    ) -> DocumentContent | DocumentLink:
        """Format a redaction for the callback."""
        if redaction.external_link:
            return DocumentLink(
                attachmentType="LINK",
                documentId=redaction.file.external_id,
                url=redaction.external_link,
            )
        else:
            return DocumentContent(
                attachmentType="BASE64",
                content=base64.b64encode(redaction.content).decode(),
                documentId=redaction.file.external_id,
            )


class RedactionProcessor(Processor):
    async def next(self, session: AsyncSession) -> Task | None:
        """Find the next task to process."""
        return await Task.next_pending_task(session)

    async def create(self, session: AsyncSession, task_id: UUID) -> UUID | None:
        """Create a new job."""
        job = Job(task_id=task_id)
        session.add(job)
        await session.flush()
        return job.id

    async def execute(self, job_id: UUID):
        self._log(logging.DEBUG, f"processing redaction job {job_id.hex()}")
        try:
            async with config.db.driver.async_session() as session:
                try:
                    await Job.start(session, job_id)
                    await session.commit()
                except Exception as e:
                    await session.rollback()
                    raise e

            async with config.db.driver.async_session() as session:
                try:
                    job = await Job.get_by_id(session, job_id)
                    if not job:
                        self._log(logging.ERROR, f"job not found: {job_id.hex()}")
                        return
                    redaction = await job.task.get_redaction(session)
                    file_bytes = redaction.file.content
                    redaction.content = await self.redact_document(file_bytes)
                    await Job.success(session, job_id)
                    await session.commit()
                    # Notify the callback processor that there's work to do
                    await self.check(CallbackProcessor)
                except Exception as e:
                    await session.rollback()
                    raise e

        except Exception as e:
            logger.error("Error processing job.")
            logger.exception(e)
            async with config.db.driver.async_session() as session:
                await Job.set_error(session, job_id, str(e))
                await session.commit()

    async def redact_document(self, file_bytes: bytes) -> bytes:
        """Redact a document."""
        # NOTE(jnu): for now this is just a playground implementation.
        pipeline_cfg = PipelineConfig.model_validate(
            {
                "pipe": [
                    {
                        "engine": "in:memory",
                    },
                    {
                        "engine": "extract:tesseract",
                    },
                    {
                        "engine": "redact:noop",
                        "delimiters": ["[", "]"],
                    },
                    {
                        "engine": "render:pdf",
                    },
                    {
                        "engine": "out:memory",
                    },
                ]
            }
        )
        pipeline = Pipeline(pipeline_cfg)
        input_buffer = io.BytesIO(file_bytes)
        output_buffer, _ = pipeline.run({"input_buffer": input_buffer})
        return cast(io.BytesIO, output_buffer).getvalue()
