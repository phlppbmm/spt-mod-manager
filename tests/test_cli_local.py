"""Tests for the ``sptmod local`` command group.

Covers ``add``, ``list``, ``remove`` and ``install`` through the CLI runner,
including the wording the two rules of AC3 are stated in, how a folder that has
built nothing yet reads on screen, and that a folder offering several mods
installs the one named on the command line. The commands take the game directory
from the working directory and the registered folders from the user's config
directory, which the suite's fake ``HOME`` keeps inside ``tmp_path``.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import clean_repo_at
from localsource_builds import write_build
from sptmod.cli import app

runner = CliRunner()


def _project(tmp_path: Path, name: str = "MyMod") -> Path:
    """Create an empty project folder a player would build a mod in."""
    folder = tmp_path / "projects" / name
    folder.mkdir(parents=True)
    return folder


def test_local_add_registers_the_folder_and_the_listing_says_it_has_built_nothing_yet(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the add handler is `pass`, this fails: the listing stays as empty as it
    was before. The build column must read as a normal state, so the user knows
    sptmod looked and found nothing rather than failing to look."""
    clean_repo_at(fake_game_dir)
    monkeypatch.chdir(fake_game_dir)
    folder = _project(tmp_path)
    before = runner.invoke(app, ["local", "list"])

    added = runner.invoke(app, ["local", "add", str(folder)])
    after = runner.invoke(app, ["local", "list"])

    assert added.exit_code == 0
    assert "nothing built yet" not in before.stdout
    assert "nothing built yet" in after.stdout


def test_local_list_names_the_mod_a_folder_installed(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the installed mod were not linked to its folder, this fails: the
    listing could not tell the user which mod a folder is behind."""
    clean_repo_at(fake_game_dir)
    monkeypatch.chdir(fake_game_dir)
    folder = _project(tmp_path)
    write_build(folder, "MyMod-1.0.0.zip", mod_name="MyMod", version="1.0.0")
    runner.invoke(app, ["local", "add", str(folder)])
    runner.invoke(app, ["local", "install", str(folder), "MyMod"])

    result = runner.invoke(app, ["local", "list"])

    assert result.exit_code == 0
    assert "MyMod" in result.stdout
    assert "1.0.0" in result.stdout


def test_local_add_on_a_missing_folder_exits_with_the_generic_code_and_reports_it(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the refusal were unhandled, this fails: a traceback would reach the
    user instead of one ``error:`` line on stderr."""
    clean_repo_at(fake_game_dir)
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["local", "add", str(tmp_path / "not-there")])

    assert result.exit_code == 1
    assert result.stderr.startswith("error:")


def test_local_install_installs_the_newest_build_and_names_the_archive(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the install handler is `pass`, this fails: nothing is reported as
    installed and the mod's files never reach the game directory."""
    clean_repo_at(fake_game_dir)
    monkeypatch.chdir(fake_game_dir)
    folder = _project(tmp_path)
    write_build(folder, "MyMod-1.0.0.zip", mod_name="MyMod", version="1.0.0")
    runner.invoke(app, ["local", "add", str(folder)])

    result = runner.invoke(app, ["local", "install", str(folder), "MyMod"])

    assert result.exit_code == 0
    assert "installed MyMod 1.0.0" in result.stdout
    assert "MyMod-1.0.0.zip" in result.stdout
    assert (fake_game_dir / "SPT/user/mods/MyMod/package.json").is_file()


def test_local_install_reports_what_the_build_says_about_its_spt_version(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The build declares an SPT version the installation does not have. If the
    warnings were dropped on the way out, this fails: the install would look
    entirely unremarkable and the mod would quietly not fit the game."""
    clean_repo_at(fake_game_dir)
    monkeypatch.chdir(fake_game_dir)
    folder = _project(tmp_path)
    write_build(folder, "MyMod-1.0.0.zip", mod_name="MyMod", version="1.0.0", spt_range="~3.0.0")
    runner.invoke(app, ["local", "add", str(folder)])

    result = runner.invoke(app, ["local", "install", str(folder), "MyMod"])

    assert result.exit_code == 0
    assert "warning:" in result.stdout


def test_local_install_calls_the_second_run_a_reinstall(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Taking the old build out and putting the new one in is one step for the
    user: if that were two commands, this fails because the second run would
    report a conflict instead of a finished reinstall."""
    clean_repo_at(fake_game_dir)
    monkeypatch.chdir(fake_game_dir)
    folder = _project(tmp_path)
    write_build(folder, "MyMod-1.0.0.zip", mod_name="MyMod", version="1.0.0", mtime=1.0)
    runner.invoke(app, ["local", "add", str(folder)])
    runner.invoke(app, ["local", "install", str(folder), "MyMod"])
    write_build(folder, "MyMod-2.0.0.zip", mod_name="MyMod", version="2.0.0", mtime=2.0)

    result = runner.invoke(app, ["local", "install", str(folder), "MyMod"])

    assert result.exit_code == 0
    assert "reinstalled MyMod 2.0.0" in result.stdout


def test_local_install_without_a_build_explains_that_nothing_was_built(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the refusal were unhandled, this fails: the user sees a traceback
    instead of a sentence naming the folder they have to build in."""
    clean_repo_at(fake_game_dir)
    monkeypatch.chdir(fake_game_dir)
    folder = _project(tmp_path)
    runner.invoke(app, ["local", "add", str(folder)])

    result = runner.invoke(app, ["local", "install", str(folder), "MyMod"])

    assert result.exit_code == 1
    assert "has not built 'MyMod'" in " ".join(result.stderr.split())


def test_local_install_installs_the_mod_named_on_the_command_line(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A project folder builds several mods: if the command took the newest
    archive of the folder, this fails with the other mod installed."""
    clean_repo_at(fake_game_dir)
    monkeypatch.chdir(fake_game_dir)
    folder = _project(tmp_path)
    write_build(folder, "Goons-2.1.0.zip", mod_name="Goons", version="2.1.0", mtime=1.0)
    write_build(folder, "Tooltips-1.0.0.zip", mod_name="Tooltips", version="1.0.0", mtime=2.0)
    runner.invoke(app, ["local", "add", str(folder)])

    result = runner.invoke(app, ["local", "install", str(folder), "Goons"])

    assert result.exit_code == 0
    assert "installed Goons 2.1.0" in result.stdout


def test_local_list_shows_every_mod_a_folder_offers(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the listing reported one build per folder, this fails: the second mod
    would be invisible and the player could not install it by name."""
    clean_repo_at(fake_game_dir)
    monkeypatch.chdir(fake_game_dir)
    folder = _project(tmp_path)
    write_build(folder, "Goons-2.1.0.zip", mod_name="Goons", version="2.1.0")
    write_build(folder, "Tooltips-1.0.0.zip", mod_name="Tooltips", version="1.0.0")
    runner.invoke(app, ["local", "add", str(folder)])

    result = runner.invoke(app, ["local", "list"])

    assert result.exit_code == 0
    assert "Goons" in result.stdout
    assert "Tooltips" in result.stdout


def test_local_remove_uninstalls_the_mod_and_says_so(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Removing a folder takes its mods out with it: if the wording or the
    uninstall were missing, this fails and the user would be left with a mod
    nothing records the origin of."""
    clean_repo_at(fake_game_dir)
    monkeypatch.chdir(fake_game_dir)
    folder = _project(tmp_path)
    write_build(folder, "MyMod-1.0.0.zip", mod_name="MyMod", version="1.0.0")
    runner.invoke(app, ["local", "add", str(folder)])
    runner.invoke(app, ["local", "install", str(folder), "MyMod"])

    result = runner.invoke(app, ["local", "remove", str(folder)])

    assert result.exit_code == 0
    assert "uninstalled with it" in result.stdout
    assert "folder off this list" in result.stdout
    assert not (fake_game_dir / "SPT/user/mods/MyMod").exists()


def test_local_remove_of_an_unregistered_folder_reports_it_on_stderr(
    fake_game_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the refusal were unhandled, this fails: the user sees a traceback
    instead of one ``error:`` line."""
    clean_repo_at(fake_game_dir)
    monkeypatch.chdir(fake_game_dir)

    result = runner.invoke(app, ["local", "remove", str(_project(tmp_path))])

    assert result.exit_code == 1
    assert result.stderr.startswith("error:")
