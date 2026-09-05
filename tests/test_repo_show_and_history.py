"""Tests for Repo.show_files, Repo.show_file_content and Repo.last_touching."""

from pathlib import Path

import pytest

from sptmod.core.errors import GitError
from sptmod.core.models import ModFile
from sptmod.core.repo import Repo

RELATIVE = Path("SPT/user/mods/ModA/file.txt")


def _write(repo: Repo, relative: Path, content: str) -> None:
    """Write text file to repo, creating parent directories as needed.

    Creates full.parent with mkdir(parents=True, exist_ok=True) and writes
    content to repo.path/relative as UTF-8 text.
    """
    full = repo.path / relative
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")


def test_show_files_reports_added_file(initialized_repo: Repo) -> None:
    """Repo.show_files() marks newly added files with change='added'."""
    _write(initialized_repo, RELATIVE, "v1\n")
    commit_hash = initialized_repo.commit(
        "mod: ModA install",
        trailers={"Mod": "ModA", "Mod-Version": "1.0.0"},
        paths=[RELATIVE],
    )

    files = initialized_repo.show_files(commit_hash)

    assert files == [ModFile(path=RELATIVE, change="added")]


def test_show_files_reports_modified_file(initialized_repo: Repo) -> None:
    """Repo.show_files() marks changed files with change='modified'."""
    _write(initialized_repo, RELATIVE, "v1\n")
    initialized_repo.commit(
        "mod: ModA install",
        trailers={"Mod": "ModA", "Mod-Version": "1.0.0"},
        paths=[RELATIVE],
    )
    _write(initialized_repo, RELATIVE, "v2\n")
    update_hash = initialized_repo.commit(
        "mod: ModA update",
        trailers={"Mod": "ModA", "Mod-Version": "1.1.0"},
        paths=[RELATIVE],
    )

    files = initialized_repo.show_files(update_hash)

    assert files == [ModFile(path=RELATIVE, change="modified")]


def test_show_files_reports_deleted_file(initialized_repo: Repo) -> None:
    """Repo.show_files() marks deleted files with change='deleted'."""
    _write(initialized_repo, RELATIVE, "v1\n")
    initialized_repo.commit(
        "mod: ModA install",
        trailers={"Mod": "ModA", "Mod-Version": "1.0.0"},
        paths=[RELATIVE],
    )
    (initialized_repo.path / RELATIVE).unlink()
    remove_hash = initialized_repo.commit(
        "mod: ModA remove",
        trailers={"Mod": "ModA", "Mod-State": "removed"},
        paths=[RELATIVE],
    )

    files = initialized_repo.show_files(remove_hash)

    assert files == [ModFile(path=RELATIVE, change="deleted")]


def test_show_file_content_returns_historical_content_not_working_tree(
    initialized_repo: Repo,
) -> None:
    """Repo.show_file_content reads historical content from commit, not disk.

    Commits file (v1), updates to v2, then deletes. File no longer on disk.
    show_file_content(v1_hash, RELATIVE) must return "v1\\n" (from commit,
    not disk where it was deleted).
    """
    _write(initialized_repo, RELATIVE, "v1\n")
    v1_hash = initialized_repo.commit(
        "mod: ModA install",
        trailers={"Mod": "ModA", "Mod-Version": "1.0.0"},
        paths=[RELATIVE],
    )
    _write(initialized_repo, RELATIVE, "v2\n")
    initialized_repo.commit(
        "mod: ModA update",
        trailers={"Mod": "ModA", "Mod-Version": "1.1.0"},
        paths=[RELATIVE],
    )
    (initialized_repo.path / RELATIVE).unlink()
    initialized_repo.commit(
        "mod: ModA remove",
        trailers={"Mod": "ModA", "Mod-State": "removed"},
        paths=[RELATIVE],
    )

    # The file no longer exists in the working tree at all.
    assert not (initialized_repo.path / RELATIVE).exists()
    assert initialized_repo.show_file_content(v1_hash, RELATIVE) == "v1\n"


def test_show_file_content_on_a_binary_blob_raises_git_error(
    initialized_repo: Repo,
) -> None:
    """Repo.show_file_content() raises GitError for binary files."""
    binary_relative = Path("SPT/user/mods/ModA/asset.bin")
    full = initialized_repo.path / binary_relative
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(b"\x00\xff\xfe\x01binary-payload-not-valid-utf8\xf8\xff")
    commit_hash = initialized_repo.commit(
        "mod: ModA install",
        trailers={"Mod": "ModA", "Mod-Version": "1.0.0"},
        paths=[binary_relative],
    )

    with pytest.raises(GitError):
        initialized_repo.show_file_content(commit_hash, binary_relative)


def test_last_touching_returns_the_most_recent_commit_with_trailers(
    initialized_repo: Repo,
) -> None:
    """Repo.last_touching returns most recent commit with populated trailers.

    Commits file (v1.0.0), updates to v2 (v1.1.0). Calls last_touching(RELATIVE).
    Returns commit with hash=update_hash, trailers["Mod-Version"]="1.1.0".
    """
    _write(initialized_repo, RELATIVE, "v1\n")
    initialized_repo.commit(
        "mod: ModA install",
        trailers={"Mod": "ModA", "Mod-Version": "1.0.0"},
        paths=[RELATIVE],
    )
    _write(initialized_repo, RELATIVE, "v2\n")
    update_hash = initialized_repo.commit(
        "mod: ModA update",
        trailers={"Mod": "ModA", "Mod-Version": "1.1.0"},
        paths=[RELATIVE],
    )

    result = initialized_repo.last_touching(RELATIVE)

    assert result is not None
    assert result.hash == update_hash
    assert result.trailers["Mod-Version"] == "1.1.0"


def test_last_touching_follows_up_to_a_delete_commit(initialized_repo: Repo) -> None:
    """Repo.last_touching includes delete commits in history search.

    Commits file install, then delete commit. Calls last_touching(RELATIVE).
    Returns delete commit (remove_hash) with trailers["Mod-State"]="removed".
    """
    _write(initialized_repo, RELATIVE, "v1\n")
    initialized_repo.commit(
        "mod: ModA install",
        trailers={"Mod": "ModA", "Mod-Version": "1.0.0"},
        paths=[RELATIVE],
    )
    (initialized_repo.path / RELATIVE).unlink()
    remove_hash = initialized_repo.commit(
        "mod: ModA remove",
        trailers={"Mod": "ModA", "Mod-State": "removed"},
        paths=[RELATIVE],
    )

    result = initialized_repo.last_touching(RELATIVE)

    assert result is not None
    assert result.hash == remove_hash
    assert result.trailers["Mod-State"] == "removed"


def test_last_touching_returns_none_for_an_untouched_path(
    initialized_repo: Repo,
) -> None:
    """Repo.last_touching returns None when path has no commit history.

    Commits one file, then calls last_touching on a different path that was
    never committed. Must return None (untouched edge case).
    """
    _write(initialized_repo, RELATIVE, "v1\n")
    initialized_repo.commit(
        "mod: ModA install",
        trailers={"Mod": "ModA", "Mod-Version": "1.0.0"},
        paths=[RELATIVE],
    )

    result = initialized_repo.last_touching(Path("SPT/user/mods/NeverTouched/file.txt"))

    assert result is None
