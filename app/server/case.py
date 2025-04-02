import asyncio
import hashlib
import logging
from typing import Any, Callable, Coroutine, NamedTuple, TypeVar, cast, overload

from bc2.core.common.name_map import IdToMaskMap, IdToNameMap, NameToMaskMap
from celery.result import AsyncResult

from .config import config
from .enumerator import RoleEnumerator
from .generated.models import HumanName, MaskedSubject, OutputDocument, RedactionTarget
from .name import human_name_to_str
from .store import SimpleMapping, StoreSession

logger = logging.getLogger(__name__)


_DEFAULT_TTL = int(config.queue.task.retention_time_seconds)


SavedMask = NamedTuple("SavedMask", [("role", str), ("mask", str), ("name", str)])
"""Information about a masked annotation saved in our DB."""


class MaskInfo:
    """A collection of masks for a case."""

    def __init__(
        self,
        initial: dict[bytes, SavedMask] | None = None,
        extra_placeholders: dict[str, str] | None = None,
    ):
        """Initialize the store optionally with a dictionary of masks.

        Args:
            initial (dict[bytes, SavedMask], optional): The initial masks.
        """
        self._masks = initial or {}
        self._extra_placeholders = extra_placeholders or {}

    def get(self, subject_id: bytes) -> SavedMask:
        """Get the mask info for a subject by ID.

        Args:
            subject_id (bytes): The subject ID.

        Returns:
            SavedMask: The mask info.
        """
        return self._masks[subject_id]

    def set(self, subject_id: bytes, mask_info: SavedMask) -> None:
        """Set the mask info for a subject by ID.

        Args:
            subject_id (bytes): The subject ID.
            mask_info (SavedMask): The mask info.
        """
        self._masks[subject_id] = mask_info

    def get_name_mask_map(self) -> NameToMaskMap:
        """Get the map from real names to masks.

        Example:
            {
                "John Doe": "Victim 1",
                "Jane Doe": "Witness 1",
            }

        Returns:
            NameToMaskMap: The map from real names to masks.
        """
        # Get all the placeholders that have IDs associated with them
        placeholders = {v.name: v.mask for v in self._masks.values()}
        # Merge with all the extra placeholders that we're not sure if we have IDs for.
        placeholders.update(self._extra_placeholders)
        return NameToMaskMap(placeholders)

    def get_id_name_map(self) -> IdToNameMap:
        """Get the map from subject IDs to real names.

        Example:
            {
                "1": "John Doe",
                "2": "Jane Doe",
            }

        Returns:
            IdToNameMap: The map from subject IDs to real names.
        """
        return IdToNameMap({k.decode(): v.name for k, v in self._masks.items()})


F_sync = TypeVar("F_sync", bound=Callable[..., Any])
F_async = TypeVar("F_async", bound=Callable[..., Coroutine[Any, Any, Any]])


@overload
def ensure_init(func: F_sync) -> F_sync: ...


@overload
def ensure_init(func: F_async) -> F_async: ...


def ensure_init(func: Any) -> Any:
    """Decorator to ensure that the case store is initialized.

    Args:
        func: The function to wrap.

    Returns:
        The wrapped function.
    """
    # Check if the func is async
    if hasattr(func, "__await__"):

        async def async_wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            if not self.inited:
                raise ValueError("Case store not initialized")
            return await func(self, *args, **kwargs)

        return async_wrapper
    else:

        def sync_wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
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

    @classmethod
    async def save_key(
        cls, store: StoreSession, key: str, value: bytes, ttl: int = _DEFAULT_TTL
    ) -> str:
        """Save a key-value pair in the store.

        Args:
            store (StoreSession): The store.
            key (str): The key.
            value (str): The value.

        Returns:
            Key under which the data was saved.
        """
        await store.set(key, value)
        t = await store.time()
        await store.expire_at(key, t + ttl)
        return key

    @classmethod
    async def get(cls, store: StoreSession, key: str) -> bytes | None:
        """Get a key-value pair from the store.

        Args:
            store (StoreSession): The store.
            key (str): The key.

        Returns:
            bytes | None: The value if it exists.
        """
        return await store.get(key)

    @classmethod
    async def save_blob(
        cls, store: StoreSession, blob: bytes, ttl: int = _DEFAULT_TTL
    ) -> str:
        """Save a blob of data in the store.

        Args:
            store (StoreSession): The store.
            blob (bytes): The blob of data.

        Returns:
            str: The key under which the data was saved.
        """
        key = hashlib.sha256(blob).hexdigest()
        return await cls.save_key(store, key, blob, ttl=ttl)

    async def init(
        self, jurisdiction_id: str, case_id: str, ttl: int = _DEFAULT_TTL
    ) -> None:
        """Initialize the case store.

        Args:
            jurisdiction_id (str): The jurisdiction ID.
            case_id (str): The case ID.
            ttl (int, optional): The time-to-live in seconds.

        Returns:
            None
        """
        if self.inited:
            return
        self.jurisdiction_id = jurisdiction_id
        self.case_id = case_id
        self.expires_at = await self._set_expiration(ttl)
        logger.debug("CaseStore initialized, will expire at %d", self.expires_at)

    @ensure_init
    async def _set_expiration(self, ttl: int) -> int:
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
    async def get_masked_names(self) -> list[MaskedSubject]:
        """Get the aliases for a case.

        Returns:
            list[MaskedSubject]: The masked subjects.
        """
        masks = await self.store.hgetall(self.key("mask"))
        return [
            MaskedSubject(
                subjectId=k.decode("utf-8"), alias=v.decode("utf-8") if v else ""
            )
            for k, v in masks.items()
        ]

    @ensure_init
    async def save_masked_name(self, subject_id: str, mask: str) -> None:
        """Save a masked name for a subject.

        Returns:
            None
        """
        await self.save_masked_names({subject_id: mask})

    @ensure_init
    async def save_masked_names(self, masks: SimpleMapping | IdToMaskMap) -> None:
        """Save a list of masked names for a case.

        Args:
            masks (SimpleMapping | IdToMaskMap): The masks to save.

        Returns:
            None
        """
        if not masks:
            return
        simple_masks = masks._map.copy() if isinstance(masks, IdToMaskMap) else masks
        mapping_key = self.key("mask")
        await self.store.hsetmapping(mapping_key, simple_masks)
        await self.store.expire_at(mapping_key, self.expires_at)

    @ensure_init
    async def save_placeholders(self, masks: SimpleMapping | NameToMaskMap) -> None:
        """Save a list of placeholders for a case.

        Args:
            masks (SimpleMapping | NameToMaskMap): The masks to save.

        Returns:
            None
        """
        if not masks:
            return
        simple_masks = masks._map.copy() if isinstance(masks, NameToMaskMap) else masks
        mapping_key = self.key("placeholders")
        await self.store.hsetmapping(mapping_key, simple_masks)
        await self.store.expire_at(mapping_key, self.expires_at)

    @ensure_init
    async def save_result_doc(self, doc_id: str, doc: OutputDocument) -> None:
        """Save a result ID for a case.

        Args:
            doc_id (str): The document ID.
            doc (OutputDocument): The document.

        Returns:
            None
        """
        k = self.key("result:" + doc_id)
        serialized_doc = doc.model_dump_json()
        await self.store.set(k, serialized_doc)
        await self.store.expire_at(k, self.expires_at)

    @ensure_init
    async def get_result_doc(self, doc_id: str) -> OutputDocument | None:
        """Get the result ID for a case.

        Returns:
            OutputDocument: The document.
        """
        k = self.key("result:" + doc_id)
        serialized_doc = await self.store.get(k)
        if not serialized_doc:
            return None
        return OutputDocument.model_validate_json(serialized_doc)

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
    async def get_mask_info(self) -> MaskInfo:
        """Get information about masks for a case.

        Returns:
            MaskInfo: Description of all masks for a case.
        """
        # id_to_role
        # ---
        # A map from subject ID to role, e.g.:
        # {"1": "judge", "2": "attorney"}
        #
        # id_to_mask
        # ---
        # A map from subject ID to an enumerated role used in the redacted text, e.g.:
        # {"1": "judge 1", "2": "attorney 1"}
        #
        # name_to_mask
        # ---
        # A map from a real name to the mask used in the redacted text, e.g.:
        # {"John Doe": "judge 1", "Jane Doe": "attorney 1"}
        #
        # NOTE: there is some overlap, especially between id_to_mask and name_to_mask.
        # this is generally not an issue for the LLM, but can be confusing to sort
        # out what this code is doing / what's stored where.
        id_to_role, id_to_mask, name_to_mask = await asyncio.gather(
            self.store.hgetall(self.key("role")),
            self.store.hgetall(self.key("mask")),
            self.store.hgetall(self.key("placeholders")),
        )
        # Every subject ID in the case that we are tracking, e.g.:
        # ["1", "2"]
        all_ids_list = list(id_to_role.keys() | id_to_mask.keys())
        # The primary name for each subject ID, e.g.:
        # ["John Doe", "Jane Doe"]
        all_names_list = await asyncio.gather(
            *[
                self.store.getmodel(
                    HumanName, self.key(f"aliases:{subject_id.decode()}:primary")
                )
                for subject_id in all_ids_list
            ]
        )
        # Map from subject ID to real primary name, e.g.:
        # {"1": "John Doe", "2": "Jane Doe"}
        id_to_real_name = {
            k: human_name_to_str(v)
            for k, v in dict(zip(all_ids_list, all_names_list)).items()
            if v
        }
        # The subject IDs that we know about roles but don't have masks for yet.
        ids_missing_masks = id_to_role.keys() - id_to_mask.keys()

        # Decode extra placeholders
        extra_placeholders = (
            {k.decode(): v.decode() for k, v in name_to_mask.items() if k and v}
            if name_to_mask
            else {}
        )

        # Start generating final metadata, e.g.:
        # MaskInfo({
        #   "j1": SavedMask(role="judge", mask="Judge 1", name="John Doe"),
        #   "a1": SavedMask(role="attorney", mask="Attorney 1", name="Jane Doe"),
        # })
        #
        # First, add the names that we have explicit masks and names for.
        metadata = MaskInfo(
            {
                k: SavedMask(
                    name=id_to_real_name.get(k, ""),
                    role=id_to_role.get(k, b"").decode(),
                    mask=id_to_mask.get(k, b"").decode(),
                )
                for k in id_to_mask.keys()
                if k in id_to_real_name
            },
            extra_placeholders=extra_placeholders,
        )

        # Next, create a RoleEnumerator so that we can generate masks for everyone else.
        try:
            role_enumerator = RoleEnumerator(v.decode() for v in id_to_mask.values())
        except ValueError as e:
            logger.error("Error initializing RoleEnumerator: %s", e)
            role_enumerator = RoleEnumerator()

        # Generate masks for the remaining IDs.
        for subject_id in ids_missing_masks:
            real_name = id_to_real_name.get(subject_id, "")
            mask = role_enumerator.next_mask(id_to_role[subject_id].decode())
            metadata.set(
                subject_id,
                SavedMask(
                    name=real_name,
                    role=id_to_role[subject_id].decode(),
                    mask=mask,
                ),
            )

        return metadata

    @ensure_init
    async def get_doc_tasks(self) -> dict[str, list[str]]:
        """Get the document tasks for a case.

        Returns:
            dict[str, list[str]]: The chain of tasks for each document.
        """
        result = await self.store.hgetall(self.key("task"))
        return {k.decode(): v.decode().split(",") for k, v in result.items()}

    @ensure_init
    async def save_doc_task(self, doc_id: str, task: AsyncResult) -> None:
        """Save a document task ID.

        Args:
            doc_id (str): The document ID.
            task (AsyncResult): The task result promise.

        Returns:
            None
        """
        k = self.key("task")
        task_ids = list[str]()
        # Flatten the list of task IDs from the result chain
        node = task
        while node:
            task_ids.insert(0, node.id)
            node = node.parent

        await self.store.hsetmapping(
            k,
            {doc_id: ",".join(task_ids)},
        )
        await self.store.expire_at(k, self.expires_at)

    @ensure_init
    async def save_objects_list(self, objects: list[RedactionTarget]) -> None:
        """Save the objects list for a case.

        Args:
            objects (list[RedactionTarget]): The objects.

        Returns:
            None
        """
        k = self.key("objects")
        for obj in objects:
            await self.store.enqueue_model(k, obj)
        await self.store.expire_at(k, self.expires_at)

    @ensure_init
    async def pop_object(self) -> RedactionTarget | None:
        """Pop an object from the objects list.

        Returns:
            RedactionTarget: The object.
        """
        k = self.key("objects")
        obj = await self.store.dequeue_model(RedactionTarget, k)
        return obj

    @ensure_init
    async def save_real_name(
        self, subject_id: str, alias: HumanName, primary: bool = False
    ) -> None:
        """Save a real name for a subject.

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
