import io
import json

from bc2 import Pipeline, PipelineConfig
from bc2.core.common.openai import FilteredContentError
from celery import Task
from celery.canvas import Signature
from celery.utils.log import get_task_logger
from pydantic import BaseModel, ValidationError

from app.func import allf

from ..case_helper import get_document_sync, save_retry_state_sync
from ..config import config
from ..generated.models import ExtractedReport
from .fetch import FetchTaskResult
from .metrics import (
    record_task_failure,
    record_task_retry,
    record_task_start,
    record_task_success,
)
from .queue import ProcessingError, queue
from .serializer import register_type

logger = get_task_logger(__name__)


class ExtractionTask(BaseModel):
    document_id: str

    def s(self) -> Signature:
        return extract.s(self)


class ExtractionTaskResult(BaseModel):
    document_id: str
    extracted_report: ExtractedReport | None = None
    errors: list[ProcessingError] = []


register_type(ExtractionTask)
register_type(ExtractionTaskResult)


@queue.task(
    bind=True,
    task_track_started=True,
    task_time_limit=300,
    task_soft_time_limit=240,
    max_retries=3,
    retry_backoff=True,
    default_retry_delay=30,
    on_retry=allf(save_retry_state_sync, record_task_retry),
    on_failure=record_task_failure,
    on_success=record_task_success,
    before_start=record_task_start,
)
def extract(
    self: Task, fetch_result: FetchTaskResult, params: ExtractionTask
) -> ExtractionTaskResult:
    """Run extraction pipeline against a fetched document."""
    if fetch_result.errors:
        return ExtractionTaskResult(
            document_id=params.document_id,
            errors=fetch_result.errors,
        )

    try:
        pipeline_cfg = PipelineConfig.model_validate(
            {
                "pipe": [
                    {"engine": "in:memory"},
                    {"engine": "out:memory"},
                ]
            }
        )
        pipeline_cfg.pipe[1:1] = config.processor.pipe

        pipeline = Pipeline(pipeline_cfg)
        input_buffer = io.BytesIO(get_document_sync(fetch_result.file_storage_id))
        output_buffer = io.BytesIO()
        pipeline.run({"in": {"buffer": input_buffer}, "out": {"buffer": output_buffer}})

        extracted_report = parse_extracted_report(output_buffer.getvalue())
        return ExtractionTaskResult(
            document_id=params.document_id,
            extracted_report=extracted_report,
        )
    except Exception as e:
        if self.request.retries >= self.max_retries or isinstance(
            e, FilteredContentError
        ):
            logger.error(
                f"Extraction failed for {params.document_id} "
                f"after {self.max_retries} retries. Error: {e}"
            )
            return ExtractionTaskResult(
                document_id=params.document_id,
                errors=[
                    *fetch_result.errors,
                    ProcessingError.from_exception("extract", e),
                ],
            )

        logger.warning(
            f"Extraction failed for {params.document_id}. This task will be retried."
        )
        logger.error("The exception that caused the failure was:")
        logger.exception(e)
        raise self.retry() from e


def parse_extracted_report(raw_output: bytes) -> ExtractedReport:
    """Parse extraction pipeline output into API model."""
    loaded = json.loads(raw_output)
    try:
        return ExtractedReport.model_validate(loaded)
    except ValidationError:
        # Some engines wrap the report in an envelope.
        if isinstance(loaded, dict) and "extractedReport" in loaded:
            return ExtractedReport.model_validate(loaded["extractedReport"])
        raise
