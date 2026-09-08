"""The JSON container primitives the profile modules read and change documents with."""

from collections.abc import Callable, Container
from dataclasses import dataclass
from typing import Final

from sptmod.core.models import Json

ID_FIELD: Final = "_id"

_TEMPLATE_FIELD: Final = "_tpl"
_PARENT_FIELD: Final = "parentId"


@dataclass(frozen=True)
class Hits:
    """What one category matched in one profile.

    Attributes:
        ids: The distinct content IDs that matched.
        count: How many profile entries those IDs account for.
    """

    ids: frozenset[str] = frozenset()
    count: int = 0


type _Match = Callable[[str, Json], set[str]]


def dict_at(scope: Json, *keys: str) -> dict[str, Json]:
    """Return the object at ``keys`` below ``scope``, empty if it is none."""
    node = scope
    for key in keys:
        if not isinstance(node, dict):
            return {}
        node = node.get(key)
    return node if isinstance(node, dict) else {}


def list_at(scope: Json, *keys: str) -> list[Json]:
    """Return the list at ``keys`` below ``scope``, empty if it is none."""
    node = scope
    for key in keys:
        if not isinstance(node, dict):
            return []
        node = node.get(key)
    return node if isinstance(node, list) else []


def counted(counts: dict[str, int]) -> Hits:
    """Return the hits of a per-ID occurrence count."""
    return Hits(frozenset(counts), sum(counts.values()))


def key_hits(container: dict[str, Json], wanted: set[str]) -> Hits:
    """Return the keys of ``container`` that are in ``wanted``, one entry each."""
    keys = {key for key in container if key in wanted}
    return Hits(frozenset(keys), len(keys))


def remove_keys(container: dict[str, Json], wanted: set[str]) -> None:
    """Delete the keys of ``container`` that are in ``wanted``."""
    for key in [key for key in container if key in wanted]:
        del container[key]


def entry_hits(container: dict[str, Json], match: _Match) -> Hits:
    """Return the hits of a container whose entries match by key and value."""
    found: set[str] = set()
    count = 0
    for key, value in container.items():
        matched = match(key, value)
        if matched:
            found |= matched
            count += 1
    return Hits(frozenset(found), count)


def remove_entries(container: dict[str, Json], match: _Match) -> None:
    """Delete the entries of ``container`` that ``match`` ties to the mod."""
    for key in [key for key, value in container.items() if match(key, value)]:
        del container[key]


def filter_list(
    owner: dict[str, Json], key: str, keep: Callable[[Json], bool]
) -> None:
    """Keep only the entries of the list ``key`` of ``owner`` that ``keep``."""
    values = owner.get(key)
    if isinstance(values, list):
        owner[key] = [value for value in values if keep(value)]


def id_in(value: Json, wanted: Container[str]) -> bool:
    """Return whether ``value`` is an ID that ``wanted`` holds."""
    return isinstance(value, str) and value in wanted


def _item_id(item: Json) -> str:
    """Return the ``_id`` of an inventory item, empty when it has none."""
    if isinstance(item, dict) and isinstance(item_id := item.get(ID_FIELD), str):
        return item_id
    return ""


def remove_items(owner: dict[str, Json], removed: Container[str]) -> None:
    """Delete the items of ``removed`` from the ``items`` list of ``owner``."""
    filter_list(owner, "items", lambda item: _item_id(item) not in removed)


def templates_below(node: Json, wanted: set[str]) -> set[str]:
    """Return every ``_tpl`` in ``wanted`` found anywhere below ``node``."""
    found: set[str] = set()
    pending: list[Json] = [node]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            template = current.get(_TEMPLATE_FIELD)
            if isinstance(template, str) and template in wanted:
                found.add(template)
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return found


def item_closure(items: list[Json], wanted: set[str]) -> set[str]:
    """Return the IDs of the items of ``wanted`` and of everything inside them.

    An item belongs to the closure when its ``_tpl`` is in ``wanted`` or when
    its ``parentId`` names an item that already belongs to it. A ``parentId``
    cycle cannot loop the walk, because every item enters the closure once.
    """
    children: dict[str, list[str]] = {}
    closure: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = item.get(ID_FIELD)
        if not isinstance(item_id, str) or not item_id:
            continue
        parent = item.get(_PARENT_FIELD)
        if isinstance(parent, str):
            children.setdefault(parent, []).append(item_id)
        template = item.get(_TEMPLATE_FIELD)
        if isinstance(template, str) and template in wanted:
            closure.add(item_id)
    pending = list(closure)
    while pending:
        for child in children.get(pending.pop(), ()):
            if child not in closure:
                closure.add(child)
                pending.append(child)
    return closure


def matching_templates(items: list[Json], wanted: set[str]) -> set[str]:
    """Return the ``_tpl`` values of ``items`` that are in ``wanted``."""
    return {
        template
        for item in items
        if isinstance(item, dict)
        and isinstance(template := item.get(_TEMPLATE_FIELD), str)
        and template in wanted
    }
