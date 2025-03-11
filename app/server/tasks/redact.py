import asyncio
import io

from bc2 import Pipeline, PipelineConfig
from bc2.core.inspect.quality import QualityReport
from bc2.core.pipeline import (
    HtmlRenderConfig,
    JsonRenderConfig,
    PdfRenderConfig,
    RenderConfig,
    TextRenderConfig,
)
from celery import Task
from celery.canvas import Signature
from celery.utils.log import get_task_logger
from pydantic import BaseModel

from app.func import allf

from ..case import CaseStore, MaskInfo
from ..case_helper import get_document_sync, save_document_sync, save_retry_state_sync
from ..config import config
from ..generated.models import OutputFormat
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


class RedactionTask(BaseModel):
    document_id: str
    jurisdiction_id: str
    case_id: str
    renderer: OutputFormat = OutputFormat.PDF

    def s(self) -> Signature:
        return redact.s(self)


class RedactionTaskResult(BaseModel):
    jurisdiction_id: str
    case_id: str
    document_id: str
    errors: list[ProcessingError] = []
    file_storage_id: str | None = None
    renderer: OutputFormat


register_type(RedactionTask)
register_type(RedactionTaskResult)


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
def redact(
    self: Task, fetch_result: FetchTaskResult, params: RedactionTask
) -> RedactionTaskResult:
    """Redact a document."""
    if fetch_result.errors:
        # If there are errors from the fetch task, pass through.
        # There's nothing to redact, but we still want to get through the
        # chain to the error callback.
        return RedactionTaskResult(
            jurisdiction_id=params.jurisdiction_id,
            case_id=params.case_id,
            document_id=params.document_id,
            errors=fetch_result.errors,
            renderer=params.renderer,
        )

    try:
        pipeline_cfg = PipelineConfig.model_validate(
            {
                "pipe": [
                    {
                        "engine": "in:memory",
                    },
                    {
                        "engine": "out:memory",
                    },
                ]
            }
        )
        # Splice in the pipeline from the config, and the rendered from the request.
        # We only fix the I/O engines.
        pipeline_cfg.pipe[1:1] = config.processor.pipe + [
            output_format_to_renderer(params.renderer)
        ]
        pipeline = Pipeline(pipeline_cfg)
        input_buffer = io.BytesIO(get_document_sync(fetch_result.file_storage_id))
        output_buffer = io.BytesIO()

        # Fetch context about the case from the database.
        mask_info = get_mask_info_sync(params.jurisdiction_id, params.case_id)
        # Run the pipeline with memory I/O.
        ctx = pipeline.run(
            {
                "in": {"buffer": input_buffer},
                "out": {"buffer": output_buffer},
                "redact": {"aliases": mask_info.get_mask_name_map()},
                "inspect": {"subjects": mask_info.get_id_name_map()},
            }
        )

        if ctx.quality:
            p_valid = 1 - config.params.max_redaction_error_rate
            check_quality(ctx.quality, p_valid=p_valid)
        else:
            logger.warning("No quality report available")

        # Inspect every annotation and merge it into the shared store.
        if ctx.annotations:
            save_annotations_sync(ctx.annotations)

        # Inspect result to get new masks
        if ctx.aliases:
            # This is a new map from subjectId -> mask.
            # Join this with the existing data we have stored.
            save_aliases_sync(ctx.annotations)

        content = output_buffer.getvalue()
        content_storage_id = save_document_sync(content)
        return RedactionTaskResult(
            jurisdiction_id=params.jurisdiction_id,
            case_id=params.case_id,
            document_id=params.document_id,
            file_storage_id=content_storage_id,
            renderer=params.renderer,
        )
    except Exception as e:
        if self.request.retries >= self.max_retries:
            logger.error(
                f"Redaction failed for {params.document_id} "
                f"after {self.max_retries} retries. Error: {e}"
            )
            new_error = ProcessingError.from_exception("redact", e)
            return RedactionTaskResult(
                jurisdiction_id=params.jurisdiction_id,
                case_id=params.case_id,
                document_id=params.document_id,
                errors=[*fetch_result.errors, new_error],
                renderer=params.renderer,
            )
        else:
            logger.warning(
                f"Redaction failed for {params.document_id}. This task will be retried."
            )
            logger.error("The exception that caused the failure was:")
            logger.exception(e)
            raise self.retry() from e


class LowQualityError(Exception):
    pass


def check_quality(quality: QualityReport, p_valid: float = 0.995):
    """Check the quality of the redaction results.

    Args:
        quality (QualityReport): The quality report.

    Raises:
        LowQualityError: If the quality is too low.
    """
    if quality.chars.p_valid < p_valid:
        raise LowQualityError(f"Quality too low ({quality.chars.p_valid} < {p_valid})")
    logger.debug(f"Quality check passed ({quality.chars.p_valid} >= {p_valid})")


def output_format_to_renderer(output_format: OutputFormat) -> RenderConfig:
    """Convert OutputFormat enum to renderer pipeline directive.

    Args:
        output_format (OutputFormat): The output format.

    Returns:
        RenderConfig: The renderer pipeline directive.
    """
    match output_format:
        case OutputFormat.PDF:
            return PdfRenderConfig()
        case OutputFormat.TEXT:
            return TextRenderConfig()
        case OutputFormat.HTML:
            return HtmlRenderConfig()
        case OutputFormat.JSON:
            return JsonRenderConfig()
        case _:
            raise ValueError(f"Unsupported output format: {output_format}")


def get_mask_info_sync(jurisdiction_id: str, case_id: str) -> MaskInfo:
    """Get the names to masks mapping from the database.

    Args:
        jurisdiction_id (str): The jurisdiction ID.
        case_id (str): The case ID.

    Returns:
        MaskInfo: Data about masks for the case.
    """

    async def _fetch():
        async with config.queue.store.driver() as store:
            async with store.tx() as tx:
                cs = CaseStore(tx)
                await cs.init(jurisdiction_id, case_id)
                return await cs.get_mask_info()

    return asyncio.run(_fetch())


def save_annotations_sync(annotations: list[dict]):
    """Merge annotations into the other context we've saved for this case.

    Args:
        annotations (list[dict]): The annotations to save.
    """
    # TODO - need to clarify which inferred annotations we want to keep, which not.
    # The inferred annotations can range from names like `Officer 1` which we do
    # certainly want to keep, to locations like `Street 1` which we probably want
    # to keep, to generic descriptors for things like hair color or language, such
    # as `Hair Color 1` which probably doesn't matter as much. The trade-off for
    # keeping everything is that it could hurt prompt performance by including
    # too much information; we'd rather have the important stuff right than
    # many things confused.


def save_aliases_sync(aliases: list[dict[str, str]]):
    """Merge alias map with existing aliases.

    Args:
        aliases (list[dict[str, str]]): The aliases to save (subjectId -> mask).
    """
    # TODO - need to clarify how much this step is necessary; perhaps it will
    # never provide more information than the input from the request body.
