"""Tests for Repo.revert and Repo.cherry_pick: happy paths and conflict handling."""

from pathlib import Path

import pytest

from sptmod.core.errors import GitError
from sptmod.core.repo import Repo


def _add_and_commit(repo: Repo, relative: Path, content: str, message: str) -> str:
    """Create file, stage it, and commit with mod metadata trailers.

    Creates repo.path/relative with content. Commits with trailers
    {"Mod": "ModA", "Mod-State": "installed"}. Returns commit hash.
    """
    full = repo.path / relative
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return repo.commit(message, trailers={"Mod": "ModA", "Mod-State": "installed"}, paths=[relative])


def _modify_and_commit(repo: Repo, relative: Path, content: str, message: str) -> str:
    """Modify existing file, stage it, and commit with mod metadata trailers.

    Overwrites repo.path/relative with content. Commits with trailers
    {"Mod": "ModA", "Mod-State": "updated"}. Returns commit hash.
    """
    full = repo.path / relative
    full.write_text(content, encoding="utf-8")
    return repo.commit(message, trailers={"Mod": "ModA", "Mod-State": "updated"}, paths=[relative])


def test_revert_removes_the_files_added_by_the_reverted_commit(
    initialized_repo: Repo,
) -> None:
    """Repo.revert inverts commit changes (added files deleted, deleted restored).

    Adds SPT/user/mods/ModA/file.txt via commit. Calls repo.revert(add_hash,
    extra_trailers={"Mod-State": "removed"}). File must not exist afterward.
    """
    relative = Path("SPT/user/mods/ModA/file.txt")
    add_hash = _add_and_commit(initialized_repo, relative, "content-v1\n", "mod: ModA install")

    initialized_repo.revert(add_hash, extra_trailers={"Mod-State": "removed"})

    assert not (initialized_repo.path / relative).exists()


def test_revert_returns_a_new_commit_hash(initialized_repo: Repo) -> None:
    """Repo.revert returns new commit hash distinct from original.

    Adds file via add_hash. Calls repo.revert(add_hash). revert_hash must be
    non-empty, not equal to add_hash.
    """
    relative = Path("SPT/user/mods/ModA/file.txt")
    add_hash = _add_and_commit(initialized_repo, relative, "content-v1\n", "mod: ModA install")

    revert_hash = initialized_repo.revert(add_hash, extra_trailers={"Mod-State": "removed"})

    assert revert_hash != ""
    assert revert_hash != add_hash


def test_revert_adds_the_extra_trailers_to_the_new_commit(
    initialized_repo: Repo,
) -> None:
    """Repo.revert includes extra_trailers in revert commit metadata.

    Calls repo.revert(add_hash, extra_trailers={"Mod-State": "removed",
    "Mod": "ModA"}). Revert commit trailers include both keys with matching values.
    """
    relative = Path("SPT/user/mods/ModA/file.txt")
    add_hash = _add_and_commit(initialized_repo, relative, "content-v1\n", "mod: ModA install")

    revert_hash = initialized_repo.revert(add_hash, extra_trailers={"Mod-State": "removed", "Mod": "ModA"})

    commits = {c.hash: c for c in initialized_repo.log()}
    assert revert_hash in commits
    assert commits[revert_hash].trailers.get("Mod-State") == "removed"
    assert commits[revert_hash].trailers.get("Mod") == "ModA"


def test_cherry_pick_reintroduces_the_removed_file(initialized_repo: Repo) -> None:
    """Repo.cherry_pick() re-applies the changes from a commit (e.g., re-enabling a mod)."""
    relative = Path("SPT/user/mods/ModA/file.txt")
    add_hash = _add_and_commit(initialized_repo, relative, "content-v1\n", "mod: ModA install")
    initialized_repo.revert(add_hash, extra_trailers={"Mod-State": "removed"})
    assert not (initialized_repo.path / relative).exists()

    initialized_repo.cherry_pick(add_hash, extra_trailers={"Mod-State": "enabled"})

    assert (initialized_repo.path / relative).read_text(encoding="utf-8") == "content-v1\n"


def test_cherry_pick_returns_a_new_commit_hash(initialized_repo: Repo) -> None:
    """Repo.cherry_pick returns new commit hash distinct from original.

    Adds, then reverts, then cherry-picks add_hash. cherry_hash must be
    non-empty, not equal to add_hash.
    """
    relative = Path("SPT/user/mods/ModA/file.txt")
    add_hash = _add_and_commit(initialized_repo, relative, "content-v1\n", "mod: ModA install")
    initialized_repo.revert(add_hash, extra_trailers={"Mod-State": "removed"})

    cherry_hash = initialized_repo.cherry_pick(add_hash, extra_trailers={"Mod-State": "enabled"})

    assert cherry_hash != ""
    assert cherry_hash != add_hash


def test_cherry_pick_adds_the_extra_trailers_to_the_new_commit(
    initialized_repo: Repo,
) -> None:
    """Repo.cherry_pick includes extra_trailers in cherry-pick commit metadata.

    Calls repo.cherry_pick(add_hash, extra_trailers={"Mod-State": "enabled"}).
    cherry_hash commit trailers include Mod-State="enabled".
    """
    relative = Path("SPT/user/mods/ModA/file.txt")
    add_hash = _add_and_commit(initialized_repo, relative, "content-v1\n", "mod: ModA install")
    initialized_repo.revert(add_hash, extra_trailers={"Mod-State": "removed"})

    cherry_hash = initialized_repo.cherry_pick(add_hash, extra_trailers={"Mod-State": "enabled"})

    commits = {c.hash: c for c in initialized_repo.log()}
    assert commits[cherry_hash].trailers.get("Mod-State") == "enabled"


def test_revert_conflict_raises_git_error_with_conflicting_path_and_leaves_clean_tree(
    initialized_repo: Repo,
) -> None:
    """Repo.revert() on a conflicting change raises GitError and aborts, leaving
    no merge artifacts or staged changes.
    """
    # Track the fixture's own whitelisted files (.gitignore, abc.json) first,
    # so the only untracked file below the scenario builds is the one we
    # explicitly create to prove revert doesn't absorb it.
    initialized_repo.commit("baseline", paths=None)
    relative = Path("SPT/user/mods/ModA/file.txt")
    add_hash = _add_and_commit(initialized_repo, relative, "base\n", "mod: ModA install")
    # A later commit changes the same file, so reverting the add conflicts
    # (git reports a modify/delete conflict).
    _modify_and_commit(initialized_repo, relative, "changed-by-later-commit\n", "mod: ModA update")
    # An unrelated untracked whitelisted file lying around must survive the
    # aborted revert untouched: "clean tree" means "no conflict leftovers",
    # not "no untracked files".
    unrelated = Path("SPT/user/mods/ModB/other.txt")
    (initialized_repo.path / unrelated).parent.mkdir(parents=True)
    (initialized_repo.path / unrelated).write_text("unrelated\n", encoding="utf-8")

    with pytest.raises(GitError) as exc_info:
        initialized_repo.revert(add_hash, extra_trailers={"Mod-State": "removed"})

    assert relative in exc_info.value.conflicting_paths
    assert initialized_repo.is_clean() is False
    assert initialized_repo.status().modified == []
    assert initialized_repo.status().untracked == [unrelated]


def test_revert_conflict_leaves_the_working_tree_content_untouched(
    initialized_repo: Repo,
) -> None:
    """Revert conflict leaves working tree content unchanged (pre-attempt state).

    Adds file "base\\n", modifies to "changed-by-later-commit\\n". Revert fails
    with GitError (conflict). File content must remain "changed-by-later-commit\\n".
    """
    relative = Path("SPT/user/mods/ModA/file.txt")
    add_hash = _add_and_commit(initialized_repo, relative, "base\n", "mod: ModA install")
    _modify_and_commit(initialized_repo, relative, "changed-by-later-commit\n", "mod: ModA update")

    with pytest.raises(GitError):
        initialized_repo.revert(add_hash, extra_trailers={"Mod-State": "removed"})

    assert (initialized_repo.path / relative).read_text(encoding="utf-8") == "changed-by-later-commit\n"


def test_revert_does_not_stage_an_unrelated_untracked_file(
    initialized_repo: Repo,
) -> None:
    """Repo.revert commits only reverted change, not unrelated untracked files.

    Adds file, reverts. Creates unrelated untracked SPT/user/mods/ModB/other.txt.
    revert_hash commit must not include unrelated file (stays in status().untracked).
    """
    relative = Path("SPT/user/mods/ModA/file.txt")
    add_hash = _add_and_commit(initialized_repo, relative, "content-v1\n", "mod: ModA install")
    unrelated = Path("SPT/user/mods/ModB/other.txt")
    (initialized_repo.path / unrelated).parent.mkdir(parents=True)
    (initialized_repo.path / unrelated).write_text("unrelated\n", encoding="utf-8")

    revert_hash = initialized_repo.revert(add_hash, extra_trailers={"Mod-State": "removed"})

    assert unrelated in initialized_repo.status().untracked
    committed_files = {f.path for f in initialized_repo.show_files(revert_hash)}
    assert unrelated not in committed_files


def test_cherry_pick_does_not_stage_an_unrelated_untracked_file(
    initialized_repo: Repo,
) -> None:
    """Repo.cherry_pick commits only picked change, not unrelated untracked files.

    Adds, reverts, creates unrelated untracked file. Cherry-picks original.
    cherry_hash commit must not include unrelated file.
    """
    relative = Path("SPT/user/mods/ModA/file.txt")
    add_hash = _add_and_commit(initialized_repo, relative, "content-v1\n", "mod: ModA install")
    initialized_repo.revert(add_hash, extra_trailers={"Mod-State": "removed"})
    unrelated = Path("SPT/user/mods/ModB/other.txt")
    (initialized_repo.path / unrelated).parent.mkdir(parents=True)
    (initialized_repo.path / unrelated).write_text("unrelated\n", encoding="utf-8")

    cherry_hash = initialized_repo.cherry_pick(add_hash, extra_trailers={"Mod-State": "enabled"})

    assert unrelated in initialized_repo.status().untracked
    committed_files = {f.path for f in initialized_repo.show_files(cherry_hash)}
    assert unrelated not in committed_files


def test_revert_rejects_extra_trailer_values_containing_newlines(
    initialized_repo: Repo,
) -> None:
    """Repo.revert() raises GitError when extra_trailers contain newlines."""
    relative = Path("SPT/user/mods/ModA/file.txt")
    add_hash = _add_and_commit(initialized_repo, relative, "content-v1\n", "mod: ModA install")

    with pytest.raises(GitError):
        initialized_repo.revert(add_hash, extra_trailers={"Mod-State": "removed\nMod-Author: injected"})


def test_revert_without_extra_trailers_still_succeeds(initialized_repo: Repo) -> None:
    """Repo.revert succeeds with no extra_trailers and produces revert commit.

    Calls repo.revert(add_hash) with no extra_trailers. revert_hash is non-empty,
    file is deleted, revert commit has no Mod-State trailer (caller didn't supply it).
    """
    relative = Path("SPT/user/mods/ModA/file.txt")
    add_hash = _add_and_commit(initialized_repo, relative, "content-v1\n", "mod: ModA install")

    revert_hash = initialized_repo.revert(add_hash)

    assert revert_hash != ""
    assert revert_hash != add_hash
    assert not (initialized_repo.path / relative).exists()
    commits = {c.hash: c for c in initialized_repo.log()}
    # No amend happened: the revert commit carries none of the trailers a
    # caller-supplied Mod-State would have added.
    assert "Mod-State" not in commits[revert_hash].trailers


def test_revert_when_the_abort_itself_fails_names_the_abort_failure(
    initialized_repo: Repo,
) -> None:
    """A stale ``index.lock`` (e.g. left behind by a crashed git process)
    makes both the revert attempt and its own ``--abort`` fail for the same
    reason, exercising the "conflicted and the abort failed" path distinct
    from a plain conflict (where the abort itself succeeds)."""
    relative = Path("SPT/user/mods/ModA/file.txt")
    add_hash = _add_and_commit(initialized_repo, relative, "content-v1\n", "mod: ModA install")
    (initialized_repo.path / ".git" / "index.lock").touch()

    with pytest.raises(GitError) as exc_info:
        initialized_repo.revert(add_hash)

    assert "abort failed" in str(exc_info.value)


def test_cherry_pick_conflict_raises_git_error_and_leaves_clean_tree(
    initialized_repo: Repo,
) -> None:
    """Repo.cherry_pick() on a conflicting commit raises GitError and aborts,
    leaving no merge artifacts or staged changes.
    """
    # Track the fixture's own whitelisted files first, otherwise they show up
    # as legitimately untracked ("??") and is_clean() would correctly be False.
    initialized_repo.commit("baseline", paths=None)
    relative = Path("SPT/user/mods/ModA/file.txt")
    _add_and_commit(initialized_repo, relative, "base\n", "mod: ModA install")
    diverging_hash = _modify_and_commit(initialized_repo, relative, "from-a-diverging-commit\n", "mod: ModA update-a")
    _modify_and_commit(initialized_repo, relative, "current-head-content\n", "mod: ModA update-b")

    with pytest.raises(GitError) as exc_info:
        initialized_repo.cherry_pick(diverging_hash, extra_trailers={"Mod-State": "updated"})

    assert relative in exc_info.value.conflicting_paths
    assert initialized_repo.is_clean() is True
