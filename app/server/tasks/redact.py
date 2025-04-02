import asyncio
import io

from bc2 import Pipeline, PipelineConfig
from bc2.core.common.context import Context
from bc2.core.inspect.embed import EmbedInspectConfig
from bc2.core.inspect.quality import QualityReport
from bc2.core.render import (
    HtmlRenderConfig,
    JsonRenderConfig,
    PdfRenderConfig,
    RenderConfig,
    TextRenderConfig,
)
from bc2.lib.embedding import Embedding
from celery import Task
from celery.canvas import Signature
from celery.utils.log import get_task_logger
from pydantic import BaseModel

from app.func import allf

from ..case import CaseStore, MaskInfo
from ..case_helper import get_document_sync, save_document_sync, save_retry_state_sync
from ..config import config
from ..db import DocumentEmbedding, RdbmsConfig
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
        pipe_tail = [output_format_to_renderer(params.renderer)]

        # If configured, generate an embedding before the redaction.
        if config.experiments.enabled and config.experiments.embedding:
            embedder = EmbedInspectConfig.model_validate(
                config.experiments.embedding.model_dump()
            )
            pipe_tail.insert(0, embedder)

        pipeline_cfg.pipe[1:1] = config.processor.pipe + pipe_tail

        pipeline = Pipeline(pipeline_cfg)
        input_buffer = io.BytesIO(get_document_sync(fetch_result.file_storage_id))
        output_buffer = io.BytesIO()

        # Fetch context about the case from the database.
        mask_info = get_mask_info_sync(params.jurisdiction_id, params.case_id)
        placeholders = mask_info.get_name_mask_map()
        subject_ids = mask_info.get_id_name_map()
        # Run the pipeline with memory I/O.
        ctx = pipeline.run(
            {
                "in": {"buffer": input_buffer},
                "out": {"buffer": output_buffer},
                "redact": {"placeholders": placeholders},
                "inspect": {
                    "subjects": subject_ids,
                    "placeholders": placeholders,
                },
            }
        )

        # Check quality metrics to see if we should reject this redaction.
        if ctx.quality:
            p_valid = 1 - config.params.max_redaction_error_rate
            check_quality(ctx.quality, p_valid=p_valid)
        else:
            logger.warning("No quality report available")

        # Persist info that we've inferred about the case from this document.
        # This is *ephemeral* data that will inform future redactions of
        # documents within this case, but will *not* be saved for research purposes.
        save_inferred_case_data_sync(params.jurisdiction_id, params.case_id, ctx)

        # Save the embedding for the document, if one was generated. This is saved
        # to the RDBMS for research purposes.
        if ctx.embedding:
            save_embedding_sync(config.experiments.store, params, ctx.embedding)

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


def save_embedding_sync(
    db_config: RdbmsConfig, task: RedactionTask, embedding: Embedding
):
    """Save the embedding for the document.

    Args:
        redaction_result (RedactionTaskResult): The redaction result.
        embedding (Embedding): The embedding.
    """
    with db_config.driver.sync_session() as db:
        db.add(
            DocumentEmbedding(
                document_id=task.document_id,
                jurisdiction_id=task.jurisdiction_id,
                case_id=task.case_id,
                embedding=embedding,
                dimensions=embedding.dimensions,
                model_vendor=embedding.vendor,
                model_name=embedding.model,
                model_version=embedding.model_version,
            )
        )
        db.commit()


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


def save_inferred_case_data_sync(jurisdiction_id: str, case_id: str, context: Context):
    """Save ephemeral data inferred about the case from one document redaction.

    Args:
        jurisdiction_id (str): The jurisdiction ID.
        case_id (str): The case ID.
        context (Context): The context from the pipeline run.
    """

    async def _save():
        async with config.queue.store.driver() as store:
            async with store.tx() as tx:
                cs = CaseStore(tx)
                await cs.init(jurisdiction_id, case_id)

                if context.masked_subjects:
                    await cs.save_masked_names(context.masked_subjects)
                else:
                    logger.warning("No masked subjects found in context")

                if context.placeholders:
                    await cs.save_placeholders(context.placeholders)
                else:
                    logger.warning("No placeholders found in context")

    if not context:
        logger.warning("No context available to save")
        return

    return asyncio.run(_save())
