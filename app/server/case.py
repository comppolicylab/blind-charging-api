from typing import cast

from .generated.models import MaskedSubject
from .store import SimpleMapping, StoreSession


def key(jurisdiction_id: str, case_id: str, category: str) -> str:
    """Generate a key for a redis value.

    Args:
        jurisdiction_id (str): The jurisdiction ID.
        case_id (str): The case ID.
        category (str): The category of the task.

    Returns:
        str: The key.
    """
    return f"{jurisdiction_id}:{case_id}:{category}"


async def save_doc_task(
    store: StoreSession, jurisdiction_id: str, case_id: str, doc_id: str, task_id: str
) -> None:
    """Save a document task ID.

    Args:
        store (Store): The store session to use.
        jurisdiction_id (str): The jurisdiction ID.
        case_id (str): The case ID.
        doc_id (str): The document ID.
        task_id (str): The task ID.

    Returns:
        None
    """
    return await store.hsetmapping(
        key(jurisdiction_id, case_id, "task"),
        {doc_id: str(task_id)},
    )


async def get_doc_tasks(
    store: StoreSession, jurisdiction_id: str, case_id: str
) -> dict[str, str]:
    """Get the document tasks for a case.

    Args:
        store (Store): The store session to use.
        jurisdiction_id (str): The jurisdiction ID.
        case_id (str): The case ID.

    Returns:
        dict[str, str]: The document tasks.
    """
    result = await store.hgetall(key(jurisdiction_id, case_id, "task"))
    return {k.decode(): v.decode() for k, v in result.items()}


async def save_roles(
    store: StoreSession,
    jurisdiction_id: str,
    case_id: str,
    subject_role_mapping: dict[str, str],
) -> None:
    """Save the roles for a case.

    Args:
        store (Store): The store session to use.
        jurisdiction_id (str): The jurisdiction ID.
        case_id (str): The case ID.
        subject_role_mapping (dict[str, str]): The subject role mapping

    Returns:
        None
    """
    # dict[str, str] is not a strict subtype of Mapping[str | bytes, str | bytes],
    # even though it is fully compatible.
    # https://stackoverflow.com/a/72841649
    srm = cast(SimpleMapping, subject_role_mapping)
    return await store.hsetmapping(key(jurisdiction_id, case_id, "role"), srm)


async def get_aliases(
    store: StoreSession, jurisdiction_id: str, case_id: str
) -> list[MaskedSubject]:
    """Get the aliases for a case.

    Args:
        store (Store): The store session to use.
        jurisdiction_id (str): The jurisdiction ID.
        case_id (str): The case ID.

    Returns:
        list[MaskedSubject]: The masked subjects.
    """
    masks = await store.hgetall(key(jurisdiction_id, case_id, "mask"))
    return [MaskedSubject(subjectId=k, alias=v or "") for k, v in masks.items()]
