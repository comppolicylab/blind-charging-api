import re
from collections import defaultdict
from typing import Iterable


class RoleEnumerator:
    def __init__(self, masks: Iterable[str] | None = None):
        """Create a new RoleEnumerator.

        If existing masks are given, the RoleEnumerator will initialize
        the count at the highest number found for each role.

        Example:
            rn = RoleEnumerator(["Judge 1", "Judge 2", "Clerk 1"])
            rn.next_mask("Judge") -> "Judge 3"
            rn.next_mask("Clerk") -> "Clerk 2"

        Args:
            masks (Iterable[str], optional): The existing masks.

        Raises:
            ValueError: If a mask is invalid.
        """
        self._counters = defaultdict[str, int](int)
        if masks:
            self._init(masks)

    def _init(self, masks: Iterable[str]):
        """Initialize the counters from a list of masks.

        Args:
            masks (Iterable[str]): The existing masks.

        Raises:
            ValueError: If a mask is invalid.
        """
        for mask in masks:
            role, count = self._parse_mask(mask)
            key = self._key_from_role(role)
            self._counters[key] = max(self._counters[key], count)

    def _parse_mask(self, mask: str) -> tuple[str, int]:
        """Parse a mask into a role and count.

        Args:
            mask (str): The mask.

        Returns:
            tuple[str, int]: The role and count.
        """
        match = re.match(r"(.+?)\s+(\d+)$", mask)
        if not match:
            raise ValueError(f"Invalid mask: {mask}")
        return match.group(1), int(match.group(2))

    def next_mask(self, role: str) -> str:
        """Get the next enumerated label for a role.

        Example:
            rn.next_mask("Judge") -> "Judge 1"
            rn.next_mask("judge") -> "Judge 2"

        Args:
            role (str): The semantic role of the person

        Returns:
            str: The enumerated label
        """
        key = self._key_from_role(role)
        label = self._label_from_role(role)
        self._counters[key] += 1
        return f"{label} {self._counters[key]}"

    def _key_from_role(self, role: str) -> str:
        """Normalize a role into a key for the counter.

        Should gloss over minor differences in role names, like case
        and whitespace.

        Args:
            role (str): The role.

        Returns:
            str: The key.
        """
        return re.sub(r"\s+", "", role.lower())

    def _label_from_role(self, role: str) -> str:
        """Normalize a role into a label for the mask.

        Should normalize the role name into a human readable label.

        Args:
            role (str): The role.

        Returns:
            str: The label.
        """
        return role.strip().title()
