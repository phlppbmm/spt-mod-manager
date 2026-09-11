"""In-process ``sptmod rpc <name>`` tests for the four local mod source
functions and their error envelopes (``LOCAL_SOURCE_NOT_FOUND``,
``LOCAL_SOURCE_NOT_BUILT``, ``INVALID_PATH``). The behaviour of the service
functions themselves is covered in ``tests/test_localsources_store.py`` and
``tests/test_localsources_install.py``; this file pins the RPC contract only.
"""

from pathlib import Path

from conftest import clean_repo_at, invoke_rpc
from fixtures_rpc import assert_progress_then_result
from localsource_builds import write_build
from sptmod.core.repo import Repo


def _project(tmp_path: Path, name: str = "MyMod") -> Path:
    """Create an empty project folder a player would build a mod in."""
    folder = tmp_path / "projects" / name
    folder.mkdir(parents=True)
    return folder


def test_add_local_source_rpc_answers_with_the_whole_list(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """If the body is `pass`, this fails: the answer holds no folder, so the
    desktop app could not render the list it just added to."""
    folder = _project(tmp_path)

    exit_code, lines = invoke_rpc(
        "add_local_source",
        {"path": str(clean_repo.path), "folder": str(folder), "store_path": str(local_source_store)},
    )

    assert exit_code == 0
    assert [entry["folder"] for entry in lines[-1]["data"]] == [str(folder)]


def test_list_local_sources_rpc_reports_the_offered_mods_and_the_installed_one(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """If the enrichment were dropped, this fails: the answer would carry the
    folder alone, and the app could show neither the mods it offers nor which of
    them is installed."""
    folder = _project(tmp_path)
    archive = write_build(folder, "MyMod-1.0.0.zip", mod_name="MyMod", version="1.0.0")
    write_build(folder, "Other-2.0.0.zip", mod_name="Other", version="2.0.0")
    invoke_rpc(
        "add_local_source",
        {"path": str(clean_repo.path), "folder": str(folder), "store_path": str(local_source_store)},
    )
    invoke_rpc(
        "install_local_source",
        {
            "path": str(clean_repo.path),
            "folder": str(folder),
            "mod": "MyMod",
            "store_path": str(local_source_store),
        },
    )

    exit_code, lines = invoke_rpc(
        "list_local_sources",
        {"path": str(clean_repo.path), "store_path": str(local_source_store)},
    )

    assert exit_code == 0
    offered = lines[-1]["data"][0]["mods"]
    assert [item["name"] for item in offered] == ["MyMod", "Other"]
    assert offered[0]["archive"] == str(archive)
    assert offered[0]["built_at"]
    assert offered[0]["mod"]["name"] == "MyMod"
    assert offered[1]["mod"] is None


def test_list_local_sources_rpc_reports_a_folder_without_a_build_as_empty(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """A folder that has built nothing is a normal state: if it were an error,
    this fails because the listing would answer with an error envelope instead
    of a source offering nothing."""
    folder = _project(tmp_path)
    invoke_rpc(
        "add_local_source",
        {"path": str(clean_repo.path), "folder": str(folder), "store_path": str(local_source_store)},
    )

    exit_code, lines = invoke_rpc(
        "list_local_sources",
        {"path": str(clean_repo.path), "store_path": str(local_source_store)},
    )

    assert exit_code == 0
    assert lines[-1]["data"][0]["mods"] == []


def test_remove_local_source_rpc_answers_with_the_remaining_list(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """If the body is `pass`, this fails: the removed folder is still part of
    the answer the app renders the list from."""
    folder = _project(tmp_path)
    invoke_rpc(
        "add_local_source",
        {"path": str(clean_repo.path), "folder": str(folder), "store_path": str(local_source_store)},
    )

    exit_code, lines = invoke_rpc(
        "remove_local_source",
        {"path": str(clean_repo.path), "folder": str(folder), "store_path": str(local_source_store)},
    )

    assert exit_code == 0
    assert lines[-1]["data"] == []


def test_install_local_source_rpc_reports_progress_and_the_installed_build(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """If the progress callback were not wired, this fails: the app would get a
    result line only and could not show what a long install is doing."""
    folder = _project(tmp_path)
    write_build(folder, "MyMod-1.0.0.zip", mod_name="MyMod", version="1.0.0")
    invoke_rpc(
        "add_local_source",
        {"path": str(clean_repo.path), "folder": str(folder), "store_path": str(local_source_store)},
    )

    exit_code, lines = invoke_rpc(
        "install_local_source",
        {
            "path": str(clean_repo.path),
            "folder": str(folder),
            "mod": "MyMod",
            "store_path": str(local_source_store),
            "operation_id": "op-1",
        },
    )

    assert exit_code == 0
    assert_progress_then_result(lines)
    assert {line["operation_id"] for line in lines[:-1]} == {"op-1"}
    data = lines[-1]["data"]
    assert data["operation_id"] == "op-1"
    assert data["mod"]["name"] == "MyMod"
    assert data["mod"]["forge_id"] == ""
    assert data["replaced"] is False


def test_install_local_source_rpc_reports_a_folder_without_a_build(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """If the refusal had no code of its own, this fails: the app could not tell
    "you have not built that yet" apart from a broken archive."""
    folder = _project(tmp_path)
    invoke_rpc(
        "add_local_source",
        {"path": str(clean_repo.path), "folder": str(folder), "store_path": str(local_source_store)},
    )

    exit_code, lines = invoke_rpc(
        "install_local_source",
        {
            "path": str(clean_repo.path),
            "folder": str(folder),
            "mod": "MyMod",
            "store_path": str(local_source_store),
        },
    )

    assert exit_code == 1
    assert lines[-1]["data"]["code"] == "LOCAL_SOURCE_NOT_BUILT"


def test_install_local_source_rpc_reports_an_unregistered_folder(
    clean_repo: Repo, local_source_store: Path, tmp_path: Path
) -> None:
    """If the store lookup were skipped, this fails: any folder on the machine
    could be installed from through the RPC boundary."""
    folder = _project(tmp_path)

    exit_code, lines = invoke_rpc(
        "install_local_source",
        {
            "path": str(clean_repo.path),
            "folder": str(folder),
            "mod": "MyMod",
            "store_path": str(local_source_store),
        },
    )

    assert exit_code == 1
    assert lines[-1]["data"]["code"] == "LOCAL_SOURCE_NOT_FOUND"


def test_add_local_source_rpc_reports_a_folder_that_does_not_exist(
    fake_game_dir: Path, local_source_store: Path, tmp_path: Path
) -> None:
    """If the existence check were dropped, this fails: a path the user mistyped
    would be stored and only fail much later, at install time."""
    clean_repo_at(fake_game_dir)

    exit_code, lines = invoke_rpc(
        "add_local_source",
        {
            "path": str(fake_game_dir),
            "folder": str(tmp_path / "not-there"),
            "store_path": str(local_source_store),
        },
    )

    assert exit_code == 1
    assert lines[-1]["data"]["code"] == "INVALID_PATH"
