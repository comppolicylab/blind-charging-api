import asyncio

from .case import CaseStore
from .config import config


def get_document_sync(file_storage_id: str | None) -> bytes:
    """Get the document content from the store.

    Args:
        file_storage_id: The ID in the store where the content was saved.

    Returns:
        bytes: The content.
    """
    if not file_storage_id:
        return b""

    async def _get():
        async with config.queue.store.driver() as store:
            async with store.tx() as tx:
                return await CaseStore.get_blob(tx, file_storage_id)

    return asyncio.run(_get())


def save_document_sync(file_bytes: bytes) -> str:
    """Save the fetched document in the queue's store.

    Args:
        file_bytes: Content to save.

    Returns:
        ID in the store where the content was saved.
    """

    async def _save():
        async with config.queue.store.driver() as store:
            async with store.tx() as tx:
                return await CaseStore.save_blob(tx, file_bytes)

    return asyncio.run(_save())
