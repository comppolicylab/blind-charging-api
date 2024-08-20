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
            title=human_name.title,
            first=human_name.firstName,
            middle=human_name.middleName,
            last=human_name.lastName,
            suffix=human_name.suffix,
            nickname=human_name.nickname,
        )
    else:
        hm = human_name

    return str(hm)
