"""Tests for Repo.commit trailer handling and Repo.log trailer parsing/filtering."""

from pathlib import Path

import pytest

from sptmod.core.errors import GitError
from sptmod.core.repo import Repo

FULL_TRAILERS = {
    "Mod": "SAIN",
    "Mod-Version": "3.2.1",
    "Mod-Type": "hybrid",
    "Mod-Author": "Solarint",
    "Mod-SPT": "~4.1.0",
    "Mod-Source": "https://example.invalid/SAIN-3.2.1.7z",
    "Mod-Forge-Id": "1234",
    "Mod-Forge-Version-Id": "56789",
    "Mod-Guid": "",
    "Mod-State": "installed",
}


def _add_mod_file(repo: Repo, name: str = "SAIN") -> Path:
    """Create test mod DLL and return relative path to it.

    Creates repo.path/SPT/user/mods/<name>/plugin.dll with content b"content".
    Returns Path("SPT/user/mods/<name>/plugin.dll") for use in repo.commit(paths=).
    """
    mod_dir = repo.path / "SPT" / "user" / "mods" / name
    mod_dir.mkdir(parents=True)
    (mod_dir / "plugin.dll").write_bytes(b"content")
    return Path("SPT/user/mods") / name / "plugin.dll"


def test_commit_returns_a_real_git_hash(initialized_repo: Repo) -> None:
    """Repo.commit() returns a non-empty hexadecimal commit hash."""
    _add_mod_file(initialized_repo)

    commit_hash = initialized_repo.commit("mod: SAIN 3.2.1", trailers=FULL_TRAILERS)

    assert len(commit_hash) >= 7
    assert all(c in "0123456789abcdef" for c in commit_hash)


def test_commit_trailers_roundtrip_through_log(initialized_repo: Repo) -> None:
    """Repo.commit and Repo.log preserve trailers identically in roundtrip.

    Commits FULL_TRAILERS dict (10 mod-related trailers). Calls Repo.log() and
    verifies commits[0].trailers == FULL_TRAILERS exactly.
    """
    _add_mod_file(initialized_repo)
    initialized_repo.commit("mod: SAIN 3.2.1", trailers=FULL_TRAILERS)

    commits = initialized_repo.log()

    assert len(commits) == 1
    assert commits[0].trailers == FULL_TRAILERS


def test_commit_empty_value_trailer_is_kept_as_present_but_empty(
    initialized_repo: Repo,
) -> None:
    """Repo.commit preserves empty trailer values (present key, empty string).

    Commits FULL_TRAILERS (which has Mod-Guid=""). Calls Repo.log(). Verifies
    "Mod-Guid" in commits[0].trailers and commits[0].trailers["Mod-Guid"] == "".
    """
    _add_mod_file(initialized_repo)
    initialized_repo.commit("mod: SAIN 3.2.1", trailers=FULL_TRAILERS)

    commits = initialized_repo.log()

    assert len(commits) == 1
    assert "Mod-Guid" in commits[0].trailers
    assert commits[0].trailers["Mod-Guid"] == ""


def test_commit_subject_is_preserved(initialized_repo: Repo) -> None:
    """Repo.log returns exact commit subject line without modification.

    Commits subject "mod: SAIN 3.2.1". Calls Repo.log(). Verifies
    commits[0].subject == "mod: SAIN 3.2.1" exactly.
    """
    _add_mod_file(initialized_repo)
    initialized_repo.commit("mod: SAIN 3.2.1", trailers=FULL_TRAILERS)

    commits = initialized_repo.log()

    assert len(commits) == 1
    assert commits[0].subject == "mod: SAIN 3.2.1"


def test_log_is_newest_first(initialized_repo: Repo) -> None:
    """Repo.log returns commits in reverse chronological order (newest first).

    Commits First (first_hash), then Second (second_hash). Calls Repo.log().
    Verifies len(commits) == 2, commits[0].hash == second_hash,
    commits[1].hash == first_hash.
    """
    path1 = _add_mod_file(initialized_repo, "First")
    first_hash = initialized_repo.commit("mod: First 1.0.0", trailers={**FULL_TRAILERS, "Mod": "First"}, paths=[path1])
    path2 = _add_mod_file(initialized_repo, "Second")
    second_hash = initialized_repo.commit(
        "mod: Second 1.0.0", trailers={**FULL_TRAILERS, "Mod": "Second"}, paths=[path2]
    )

    commits = initialized_repo.log()

    assert len(commits) == 2
    assert commits[0].hash == second_hash
    assert commits[1].hash == first_hash


def test_log_trailer_filter_matches_case_insensitively(initialized_repo: Repo) -> None:
    """Repo.log trailer_filter matches trailer values case-insensitively.

    Commits FULL_TRAILERS (Mod="SAIN", Mod-Type="hybrid"). Calls Repo.log(
    trailer_filter={"Mod": "sain", "Mod-Type": "HYBRID"}). Returns one commit
    with trailers["Mod"] == "SAIN" (original casing, case-insensitive match).
    """
    _add_mod_file(initialized_repo)
    initialized_repo.commit("mod: SAIN 3.2.1", trailers=FULL_TRAILERS)

    commits = initialized_repo.log(trailer_filter={"Mod": "sain", "Mod-Type": "HYBRID"})

    assert len(commits) == 1
    assert commits[0].trailers["Mod"] == "SAIN"


def test_log_trailer_filter_ands_conditions(initialized_repo: Repo) -> None:
    """Repo.log trailer_filter requires all conditions to match (AND logic).

    Commits First (Mod-Type="server") and Second (Mod-Type="client").
    Calls Repo.log(trailer_filter={"Mod": "Second", "Mod-Type": "client"}).
    Returns exactly one commit (Second), not both, proving both conditions required.
    """
    path1 = _add_mod_file(initialized_repo, "First")
    initialized_repo.commit(
        "mod: First 1.0.0",
        trailers={**FULL_TRAILERS, "Mod": "First", "Mod-Type": "server"},
        paths=[path1],
    )
    path2 = _add_mod_file(initialized_repo, "Second")
    initialized_repo.commit(
        "mod: Second 1.0.0",
        trailers={**FULL_TRAILERS, "Mod": "Second", "Mod-Type": "client"},
        paths=[path2],
    )

    commits = initialized_repo.log(trailer_filter={"Mod": "Second", "Mod-Type": "client"})

    assert len(commits) == 1
    assert commits[0].trailers["Mod"] == "Second"


def test_log_trailer_filter_excludes_non_matching_commits(
    initialized_repo: Repo,
) -> None:
    """Repo.log returns empty list when trailer_filter matches no commits.

    Commits First (Mod="First"). Calls Repo.log(trailer_filter={"Mod": "Second"}).
    Returns [] (empty), as no commit has Mod="Second".
    """
    path1 = _add_mod_file(initialized_repo, "First")
    initialized_repo.commit(
        "mod: First 1.0.0",
        trailers={**FULL_TRAILERS, "Mod": "First", "Mod-Type": "server"},
        paths=[path1],
    )

    commits = initialized_repo.log(trailer_filter={"Mod": "Second"})

    assert commits == []


def test_commit_with_explicit_paths_only_stages_those_paths(
    initialized_repo: Repo,
) -> None:
    """Repo.commit() with paths parameter commits only those files."""
    path = _add_mod_file(initialized_repo, "OnlyThis")
    unrelated = Path("SPT/user/profiles/abc.json")
    (initialized_repo.path / unrelated).write_text('{"changed": true}', encoding="utf-8")

    commit_hash = initialized_repo.commit("mod: OnlyThis 1.0.0", paths=[path])

    files = {mod_file.path for mod_file in initialized_repo.show_files(commit_hash)}
    assert files == {path}
    # The unrelated whitelisted file must not have been staged or committed
    # as a side effect: it stays untracked.
    assert unrelated in initialized_repo.status().untracked


def test_commit_with_nothing_to_commit_raises_git_error(initialized_repo: Repo) -> None:
    """Repo.commit raises GitError when paths=[] (nothing to commit).

    Calls initialized_repo.commit("mod: nothing here", paths=[]). Must raise
    GitError (no files staged, nothing to commit).
    """
    with pytest.raises(GitError):
        initialized_repo.commit("mod: nothing here", paths=[])


@pytest.mark.parametrize("bad_value", ["user\nMod-State: installed", "user\rinjected"])
def test_commit_rejects_trailer_values_containing_newlines(initialized_repo: Repo, bad_value: str) -> None:
    """Repo.commit() rejects trailer values containing line breaks to prevent injection."""
    _add_mod_file(initialized_repo)

    with pytest.raises(GitError):
        initialized_repo.commit("mod: SAIN 3.2.1", trailers={"Mod-Author": bad_value})


def test_commit_rejecting_an_injected_trailer_value_does_not_create_extra_trailers(
    initialized_repo: Repo,
) -> None:
    """Rejecting a malicious trailer prevents the commit entirely (atomicity)."""
    _add_mod_file(initialized_repo)

    with pytest.raises(GitError):
        initialized_repo.commit("mod: SAIN 3.2.1", trailers={"Mod-Author": "user\nMod-State: installed"})

    # The commit must not have gone through at all (no injected 'Mod-State').
    assert initialized_repo.log() == []


def test_commit_raises_git_error_when_git_itself_refuses_the_commit(
    initialized_repo: Repo,
) -> None:
    """A generic git-detected commit failure, distinct from the ``paths=[]``
    and trailer-newline guards: here a pre-commit hook rejects the commit."""
    _add_mod_file(initialized_repo)
    hook = initialized_repo.path / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'custom rejection message' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    with pytest.raises(GitError) as exc_info:
        initialized_repo.commit("mod: SAIN 3.2.1", trailers=FULL_TRAILERS)

    assert "custom rejection message" in str(exc_info.value)
    assert initialized_repo.log() == []
