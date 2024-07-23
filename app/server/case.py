import logging
from typing import cast

from .generated.models import HumanName, MaskedSubject
from .store import SimpleMapping, StoreSession

logger = logging.getLogger(__name__)

ONE_WEEK_S = 60 * 60 * 24 * 7
"""One week in seconds."""


def ensure_init(func):
    """Decorator to ensure that the case store is initialized.

    Args:
        func: The function to wrap.

    Returns:
        The wrapped function.
    """
    # Check if the func is async
    if hasattr(func, "__await__"):

        async def async_wrapper(self, *args, **kwargs):
            if not self.inited:
                raise ValueError("Case store not initialized")
            return await func(self, *args, **kwargs)

        return async_wrapper
    else:

        def sync_wrapper(self, *args, **kwargs):
            if not self.inited:
                raise ValueError("Case store not initialized")
            return func(self, *args, **kwargs)

        return sync_wrapper


class CaseStore:
    def __init__(self, store: StoreSession):
        self.store = store
        self.jurisdiction_id = ""
        self.case_id = ""
        self.expires_at = 0

    @property
    def inited(self) -> bool:
        return bool(self.jurisdiction_id and self.case_id)

    async def init(self, jurisdiction_id: str, case_id: str, ttl: int = ONE_WEEK_S):
        """Initialize the case store.

        Args:
            jurisdiction_id (str): The jurisdiction ID.
            case_id (str): The case ID.
            ttl (int, optional): The time-to-live in seconds. Defaults to ONE_WEEK_S.

        Returns:
            None
        """
        if self.inited:
            return
        self.jurisdiction_id = jurisdiction_id
        self.case_id = case_id
        self.expires_at = await self.set_expiration(ttl)
        logger.debug("CaseStore initialized, will expire at %d", self.expires_at)

    @ensure_init
    async def set_expiration(self, ttl: int) -> int:
        """Set the expiration time for the case.

        Returns:
            int: The expiration time, as a unix timestamp
        """
        expires_at = await self.store.time() + ttl
        k = self.key("expires")
        await self.store.set(k, "")
        await self.store.expire_at(k, expires_at)
        return expires_at

    @ensure_init
    async def get_aliases(self) -> list[MaskedSubject]:
        """Get the aliases for a case.

        Returns:
            list[MaskedSubject]: The masked subjects.
        """
        masks = await self.store.hgetall(self.key("mask"))
        return [MaskedSubject(subjectId=k, alias=v or "") for k, v in masks.items()]

    @ensure_init
    async def save_roles(
        self,
        subject_role_mapping: dict[str, str],
    ) -> None:
        """Save the roles for a case.

        Args:
            subject_role_mapping (dict[str, str]): The subject role mapping

        Returns:
            None
        """
        # dict[str, str] is not a strict subtype of Mapping[str | bytes, str | bytes],
        # even though it is fully compatible.
        # https://stackoverflow.com/a/72841649
        srm = cast(SimpleMapping, subject_role_mapping)
        k = self.key("role")
        await self.store.hsetmapping(k, srm)
        await self.store.expire_at(k, self.expires_at)

    @ensure_init
    async def get_doc_tasks(self) -> dict[str, str]:
        """Get the document tasks for a case.

        Returns:
            dict[str, str]: The document tasks.
        """
        result = await self.store.hgetall(self.key("task"))
        return {k.decode(): v.decode() for k, v in result.items()}

    @ensure_init
    async def save_doc_task(self, doc_id: str, task_id: str) -> None:
        """Save a document task ID.

        Args:
            doc_id (str): The document ID.
            task_id (str): The task ID.

        Returns:
            None
        """
        k = self.key("task")
        await self.store.hsetmapping(
            k,
            {doc_id: str(task_id)},
        )
        await self.store.expire_at(k, self.expires_at)

    @ensure_init
    async def save_alias(
        self, subject_id: str, alias: HumanName, primary: bool = False
    ) -> None:
        """Save an alias for a subject.

        Args:
            subject_id (str): The subject ID.
            alias (HumanName): The alias.
            primary (bool, optional): Whether the alias is primary. Defaults to False.

        Returns:
            None
        """
        subject_key = f"aliases:{subject_id}"

        if primary:
            k = self.key(f"{subject_key}:primary")
            await self.store.setmodel(k, alias)
            await self.store.expire_at(k, self.expires_at)

        await self.store.saddmodel(self.key(subject_key), alias)
        await self.store.expire_at(self.key(subject_key), self.expires_at)

    @ensure_init
    def key(self, category: str) -> str:
        """Generate a key for a redis value.

        Args:
            category (str): The category of the task.

        Returns:
            str: The key.
        """
        return f"{self.jurisdiction_id}:{self.case_id}:{category}"
