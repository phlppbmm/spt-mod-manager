"""Finding a mod's residue in profiles and clearing it from a profile document."""

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

from sptmod.core.game_dir import GameLayout
from sptmod.core.models import (
    ACTIVE_STATES,
    TRAILER_MOD,
    TRAILER_MOD_STATE,
    CommitInfo,
    Json,
    ProfileResidue,
    ResidueCategory,
)
from sptmod.core.profile.categories import (
    CATEGORIES,
    CHARACTERS,
    Category,
    ResidueCharacter,
    Scope,
)
from sptmod.core.profile.files import ParsedProfile, parse_profile, profile_paths
from sptmod.core.profile.ids import ModIds, all_ids, collect_mod_ids
from sptmod.core.profile.nodes import Hits, dict_at, item_closure, list_at
from sptmod.core.repo import Repo


@dataclass(frozen=True)
class CheckResult:
    """What a residue check over all mods and all profiles found.

    Attributes:
        residues: The residues per mod name, in the order the mods were
            checked. Mods without residues are absent.
        warnings: The collection warnings of every checked mod, each prefixed
            with the mod name.
    """

    residues: dict[str, list[ProfileResidue]] = field(default_factory=dict[str, list[ProfileResidue]])
    warnings: list[str] = field(default_factory=list[str])


def find_residues(profile_path: Path, ids: ModIds) -> list[ProfileResidue]:
    """Return the residues of ``ids`` in the profile file ``profile_path``.

    Searches both characters, ``pmc`` and ``scav``, and the sections both
    share. Every residue names the character it belongs to and the JSON
    pointer of the container it was found in:

    ``item``
        ``Inventory.items`` entries whose ``_tpl`` is one of the mod's item
        templates, plus every item stored inside them: an item counts as a hit
        when its chain of ``parentId`` references leads to such an item. The
        residue reports the matched templates in ``ids`` and how many items
        that includes in ``count``.
    ``encyclopedia``, ``wishlist``
        Keys of ``Encyclopedia`` and ``WishList`` that are one of the mod's
        item templates.
    ``trader``
        Keys of ``TradersInfo`` that are one of the mod's traders.
    ``trader-purchase``
        Keys of the shared ``traderPurchases`` that are one of the mod's
        traders.
    ``quest``
        ``Quests`` entries whose ``qid`` is one of the mod's quests.
    ``repeatable-quest``
        Entries of ``activeQuests`` or ``inactiveQuests`` in
        ``RepeatableQuests`` whose ``traderId`` is one of the mod's traders or
        whose ``_id``, ``templateId`` or ``questStatus.qid`` is one of its
        quests.
    ``task-counter``
        Keys of ``TaskConditionCounters`` that are one of the mod's quests, or
        whose entry has such a quest as its ``sourceId``.
    ``hideout-production``
        Keys of ``Hideout.Production`` that are one of the mod's recipes, or
        whose entry has such a recipe as its ``recipeId``.
    ``hideout-area``
        Slots of ``Hideout.Areas`` holding an item of the mod anywhere below
        them.
    ``unlocked-recipe``
        Entries of ``UnlockedInfo.unlockedProductionRecipe`` that are one of
        the mod's recipes.
    ``insured-item``
        ``InsuredItems`` entries whose ``tid`` is one of the mod's traders or
        whose ``itemId`` is an inventory item of the ``item`` category.
    ``achievement``
        Keys of the pmc's ``Achievements`` that are one of the mod's
        achievements.
    ``insurance``
        Shared ``insurance`` entries from one of the mod's traders, or holding
        one of its items together with the items stored inside it.
    ``dialogue``
        Shared ``dialogues`` of one of the mod's traders, and single messages
        whose ``templateId`` starts with one of the mod's quest IDs or whose
        ``items`` hold one of its item templates.
    ``userbuild``
        Entries of the shared ``userbuilds`` lists that use one of the mod's
        item templates.
    ``string``
        Every ID none of the categories above matched is looked for in the raw
        profile text; a hit there is reported here, because the fallback
        cannot say what the ID is used for.

    Returns at most one residue per character and category, each carrying the
    distinct IDs found and how many entries they account for. Residues are
    ordered by character — ``pmc``, then ``scav``, then the shared sections —
    and within a character in the order of the categories above; ``string`` is
    always last. Categories without a hit are absent, so an empty list means
    the profile is free of the mod's content. A profile that is missing,
    unreadable or no valid JSON yields an empty list.

    A missing container, or one of an unexpected type, is simply no hit.
    The profile file is only read, never written.
    """
    parsed = parse_profile(profile_path)
    if parsed is None:
        return []
    return _residues_in(parsed, ids)


def check_all(repo: Repo, layout: GameLayout) -> CheckResult:
    """Check every active mod against every profile without changing anything.

    Collects the IDs of each active mod from its install commit and searches
    them in every profile returned by :func:`profile_paths`. Neither the
    repository nor the profiles are modified.

    Raises:
        GitError: git failed.
    """
    profiles = [
        parsed
        for path in profile_paths(layout)
        if (parsed := parse_profile(path)) is not None
    ]
    result = CheckResult()
    for name, commit in _active_mod_commits(repo).items():
        ids = collect_mod_ids(repo, commit, layout)
        result.warnings.extend(f"{name}: {warning}" for warning in ids.warnings)
        residues = [
            residue for profile in profiles for residue in _residues_in(profile, ids)
        ]
        if residues:
            result.residues[name] = residues
    return result


@dataclass(frozen=True)
class _Cleaned:
    """A profile document freed of a mod's content, and what that cost.

    The residues carry no profile name; the caller knows the file they came
    from.

    Attributes:
        document: The cleaned copy of the profile document.
        removed: The residues the document no longer holds.
        untouched: The residues that stayed, because no category could say
            what they are used for.
    """

    document: dict[str, Json]
    removed: list[ProfileResidue]
    untouched: list[ProfileResidue]


def _residues_in(profile: ParsedProfile, ids: ModIds) -> list[ProfileResidue]:
    """Find which IDs from ``ids`` appear in ``profile``.

    Runs every category of ``CATEGORIES`` over the characters it applies to,
    character-major and category-minor, then text-searches the profile for the
    IDs no category matched. Untyped text hits are reported in the 'string'
    category.
    """
    residues: list[ProfileResidue] = []
    matched: set[str] = set()
    for entry, _, character, hits in _hits_in(profile.root, ids):
        matched |= hits.ids
        residues.append(_residue(profile.name, entry, character, hits))
    return [*residues, *_string_residue(profile.name, profile.text, ids, matched)]


def clean_document(profile: ParsedProfile, ids: ModIds) -> _Cleaned:
    """Return a copy of ``profile`` freed of the content of ``ids``.

    Every category that reports a hit also clears it, so the report is exactly
    what the copy lost and a preview can never disagree with the real run. IDs
    that no category could attribute stay: the text fallback cannot say what
    they mean, so removing them would be guesswork; it reads the same profile
    text a check reads, so both agree on what is left.

    ``profile`` itself is never changed, and the reported residues carry no
    profile name -- only the caller knows which file they came from.
    """
    document = deepcopy(profile.root)
    removed: list[ProfileResidue] = []
    matched: set[str] = set()
    for entry, scope, character, hits in _hits_in(document, ids):
        entry.remover(scope)
        matched |= hits.ids
        removed.append(_residue("", entry, character, hits))
    return _Cleaned(
        document=document,
        removed=removed,
        untouched=_string_residue("", profile.text, ids, matched),
    )


def _hits_in(
    root: dict[str, Json], ids: ModIds
) -> list[tuple[Category, Scope, ResidueCharacter, Hits]]:
    """Return every category that matches something, with its scope and hits.

    Walks the characters first, then the sections they share, and within each
    scope the categories in the order of ``CATEGORIES``. The whole document is
    read before the caller can act on the result, so a category never reports
    against what an earlier one removed.
    """
    hits_found: list[tuple[Category, Scope, ResidueCharacter, Hits]] = []
    for character, scope in _scopes(root, ids):
        for entry in CATEGORIES:
            if character not in entry.characters:
                continue
            hits = entry.finder(scope)
            if hits.ids:
                hits_found.append((entry, scope, character, hits))
    return hits_found


def _scopes(
    root: dict[str, Json], ids: ModIds
) -> list[tuple[ResidueCharacter, Scope]]:
    """Return the scopes of a profile: both characters, then the shared part.

    The inventory closure of a character is computed once here, because both
    the items themselves and the insurance entries pointing at them need it.
    """
    scopes: list[tuple[ResidueCharacter, Scope]] = []
    for character in CHARACTERS:
        node = dict_at(root, "characters", character)
        closure = item_closure(list_at(node, "Inventory", "items"), ids.items)
        scopes.append(
            (character, Scope(node=node, ids=ids, item_closure=frozenset(closure)))
        )
    scopes.append(
        (ResidueCharacter.SHARED, Scope(node=root, ids=ids, item_closure=frozenset()))
    )
    return scopes


def _residue(
    name: str, entry: Category, character: ResidueCharacter, hits: Hits
) -> ProfileResidue:
    """Return the residue one category found in one profile."""
    return ProfileResidue(
        profile=name,
        category=entry.category,
        ids=sorted(hits.ids),
        count=hits.count,
        character=character,
        path=entry.path.format(character=character),
    )


def _string_residue(
    name: str, text: str, ids: ModIds, matched: set[str]
) -> list[ProfileResidue]:
    """Return the residue of the IDs no category matched, if there is one.

    The fallback scans the whole profile text once per unmatched ID; mods add
    tens of IDs and profiles are a few megabytes, so this stays cheap.
    """
    counts = {
        found: text.count(found) for found in all_ids(ids) - matched if found in text
    }
    if not counts:
        return []
    return [
        ProfileResidue(
            profile=name,
            category=ResidueCategory.STRING,
            ids=sorted(counts),
            count=sum(counts.values()),
        )
    ]


def _active_mod_commits(repo: Repo) -> dict[str, str]:
    """Return the newest commit hash per active mod, newest mod first."""
    heads: dict[str, CommitInfo] = {}
    for commit in repo.log():
        name = commit.trailers.get(TRAILER_MOD, "")
        if name:
            heads.setdefault(name, commit)
    return {
        name: head.hash
        for name, head in heads.items()
        if head.trailers.get(TRAILER_MOD_STATE, "") in ACTIVE_STATES
    }
