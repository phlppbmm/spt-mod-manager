"""Install-plan resolution: wanted version plus its Forge dependency tree."""

from collections.abc import Sequence

from sptmod.core.errors import UnresolvableDependencyError
from sptmod.core.forge.catalog import Catalog, NoCompatibleVersionError
from sptmod.core.forge.client import DependencyNode, ForgeClient
from sptmod.core.models import InstallPlan, ModInfo, PlanEntry
from sptmod.core.modmeta import UnsupportedRangeError, version_matches


def _is_satisfied(
    node: DependencyNode, version: str, installed_mods: Sequence[ModInfo]
) -> bool:
    """Return whether an installed mod already covers the dependency node.

    Fails closed: an installed version the semver matcher cannot compare
    counts as not satisfying the dependency, so the plan reinstalls it.
    """
    match = next(
        (
            mod
            for mod in installed_mods
            if (mod.forge_id and mod.forge_id == str(node.id))
            or mod.name.casefold() == node.name.casefold()
        ),
        None,
    )
    if match is None:
        return False
    try:
        return version_matches(match.version, f">={version}")
    except UnsupportedRangeError:
        return False


def _collect(
    node: DependencyNode,
    path: frozenset[int],
    planned: set[int],
    installed_mods: Sequence[ModInfo],
    entries: list[PlanEntry],
) -> None:
    """Recursively collect a dependency node and its unsatisfied dependencies.

    Validates the node for cycles (raises if node.id in path), conflicts,
    and version compatibility. Recursively processes child dependencies
    before this node (dependencies-first). Skips nodes already in planned
    (diamond dedup) and nodes satisfied by installed mods.

    Args:
        node: The dependency node to process.
        path: Node IDs from root to node; used for cycle detection.
        planned: Node IDs already processed; modified in-place.
        installed_mods: Mods currently installed; checked for satisfaction.
        entries: Plan entries to populate; modified in-place.

    Raises:
        UnresolvableDependencyError: For cycles, conflicts, or missing versions.
    """
    if node.id in path:
        raise UnresolvableDependencyError(node.name, "it takes part in a cycle")
    if node.id in planned:
        return
    if node.conflict:
        raise UnresolvableDependencyError(
            node.name, "Forge reports conflicting version constraints"
        )
    version = node.latest_compatible_version
    if version is None:
        raise UnresolvableDependencyError(
            node.name, "Forge has no compatible version for this SPT release"
        )
    for child in node.dependencies:
        _collect(child, path | {node.id}, planned, installed_mods, entries)
    planned.add(node.id)
    if _is_satisfied(node, version.version, installed_mods):
        return
    entries.append(
        PlanEntry(
            mod_id=node.id,
            name=node.name,
            guid=node.guid,
            version=version.version,
            version_id=version.id,
            link=version.link,
            content_length=version.content_length,
            is_dependency=True,
        )
    )


def plan_install(
    mod_id_or_name: int | str,
    wanted_version: str | None,
    catalog: Catalog,
    client: ForgeClient,
    installed_mods: Sequence[ModInfo],
    spt_version: str,
) -> InstallPlan:
    """Build the install plan for a mod and its missing dependencies.

    The target version comes from the local catalog. Its dependency tree is
    queried once from ``/mods/dependencies`` with the ``identifier:version``
    pair of the target; the response holds the direct dependencies of the
    target, which are walked recursively, so dependencies of dependencies end
    up in the plan as well. Every node is visited at most once and a node
    reappearing on the current path is a cycle. A response omitting the
    queried pair means Forge cannot resolve the target at all, which is
    distinct from an empty list (no dependencies).

    A dependency counts as satisfied, and is left out of the plan, when a mod
    with its Forge ID (or, without one, its name matched case-insensitively) is
    installed at a version that is not older than the version Forge picked for
    the node; an outdated installed dependency stays in the plan.

    Args:
        mod_id_or_name: The numeric Forge mod ID or the mod name of the target.
        wanted_version: The exact version to install, or None for the highest
            compatible one.
        catalog: The local catalog the target version is resolved against.
        client: The Forge client used for the dependency query.
        installed_mods: The mods currently installed in the game repository;
            used to skip already satisfied dependencies.
        spt_version: The SPT version to resolve for; must exactly match a
            published SPT version.

    Returns:
        The plan with dependencies before the mods requiring them and the
        requested mod last.

    Raises:
        NoCompatibleVersionError: The catalog knows no version of the target
            matching ``wanted_version`` and ``spt_version``.
        UnresolvableDependencyError: The response omits the queried pair, or a
            dependency is flagged as a conflict, has no compatible version for
            ``spt_version``, or takes part in a dependency cycle. The error
            names the mod it is about.
        ForgeUnavailableError: The Forge API is unreachable.
        ForgeApiError: The Forge API answered with an error envelope.
    """
    mod = (
        catalog.get_mod(mod_id_or_name)
        if isinstance(mod_id_or_name, int)
        else catalog.find_mod(mod_id_or_name)
    )
    if mod is None:
        raise NoCompatibleVersionError(
            f"{mod_id_or_name!r} is not in the local catalog"
        )
    version = catalog.resolve_version(
        mod.id, wanted=wanted_version, spt_version=spt_version
    )

    pair = f"{mod.id}:{version.version}"
    trees = client.dependencies([(str(mod.id), version.version)], spt_version)

    if pair not in trees:
        raise UnresolvableDependencyError(
            mod.name, f"Forge cannot resolve it for SPT {spt_version}"
        )

    entries: list[PlanEntry] = []
    planned: set[int] = {mod.id}
    for node in trees[pair]:
        _collect(node, frozenset({mod.id}), planned, installed_mods, entries)

    entries.append(
        PlanEntry(
            mod_id=mod.id,
            name=mod.name,
            guid=mod.guid,
            version=version.version,
            version_id=version.id,
            link=version.link,
            content_length=version.content_length,
            is_dependency=False,
        )
    )
    return InstallPlan(entries=entries, spt_version=spt_version)
