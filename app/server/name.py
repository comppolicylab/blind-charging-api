from nameparser import HumanName

from .generated.models import HumanName as HumanNameModel


def human_name_to_str(human_name: HumanNameModel | HumanName) -> str:
    """Convert a HumanName to a string.

    Args:
        human_name (HumanNameModel | HumanName): The HumanName to convert.

    Returns:
        str: The string representation.
    """
    if isinstance(human_name, HumanNameModel):
        hm = HumanName(
            title=human_name.root.title,
            first=human_name.root.firstName,
            middle=human_name.root.middleName,
            last=human_name.root.lastName,
            suffix=human_name.root.suffix,
            nickname=human_name.root.nickname,
        )
    else:
        hm = human_name

    return str(hm)
