import io

from bc2 import Pipeline, PipelineConfig
from bc2.core.common.openai import FilteredContentError
from celery import Task
from celery.canvas import Signature
from celery.utils.log import get_task_logger
from pydantic import BaseModel

from app.func import allf

from ..case_helper import get_document_sync, save_retry_state_sync
from ..config import config
from ..extracted_report import parse_extracted_report
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


def derive_extract_pipeline(pipe: list[dict]) -> list[dict]:  # noqa: C901
    """Derive the extract pipeline from the redaction pipe.

    Args:
        pipe: The redaction pipe to derive the extract pipeline from.

    Returns:
        The extract pipeline, without I/O engines.

    Raises:
        RuntimeError: If the extract pipeline cannot be derived.
    """
    # NOTE(jnu): For an MVP implementation, to limit complexity in the config,
    # we will inspect the redaction pipe to pull keys and urls for services,
    # and format them into a viable extract pipeline.
    #
    # In the future we should define a completely separate config for this.
    analyze_config: dict = {
        "engine": "analyze:azuredi",
        "kv": True,
        "model": "prebuilt-layout",
    }
    analyze_config_shared_keys = {"endpoint", "api_key", "api_version", "locale"}
    ontology_client: dict = {}
    ontology_generator: dict = {
        "method": "chat",
        "temperature": 0.0,
        "system": {"prompt_id": "ontology_20260415_1"},
    }
    ontology_config: dict = {
        "engine": "ontology:openai",
        "client": ontology_client,
        "generator": ontology_generator,
    }
    generator_config_shared_keys = {"model", "openai_model", "max_tokens"}
    client_config_shared_keys = {"azure_endpoint", "api_key", "api_version"}
    required_configs = {"analyze:azuredi", "parse:openai"}
    for step in pipe:
        if step["engine"] == "analyze:azuredi":
            required_configs.remove("analyze:azuredi")
            for key in analyze_config_shared_keys:
                if key in step:
                    analyze_config[key] = step[key]
        elif step["engine"] == "parse:openai":
            required_configs.remove("parse:openai")
            for key in generator_config_shared_keys:
                if key in step.get("generator", {}):
                    ontology_generator[key] = step["generator"][key]
            for key in client_config_shared_keys:
                if key in step.get("client", {}):
                    ontology_client[key] = step["client"][key]

    if required_configs:
        raise RuntimeError(
            f"Unable to derive extract pipeline: {required_configs} missing from config"
        )

    return [
        analyze_config,
        ontology_config,
    ]


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
                    *(derive_extract_pipeline(config.processor.pipe)),
                    {"engine": "out:memory"},
                ]
            }
        )

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
