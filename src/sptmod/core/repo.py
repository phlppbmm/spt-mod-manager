"""The git layer: every raw git call of sptmod lives here."""

import subprocess
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

from sptmod.core.errors import GitError, NotAGameDirError
from sptmod.core.models import CommitInfo, FileChange, ModFile, RepoStatus

if TYPE_CHECKING:
    from sptmod.core.game_dir import GameLayout


def gitignore_whitelist(runtime_dir_name: str) -> str:
    """Return the whitelist ``.gitignore`` for a runtime directory name.

    ``runtime_dir_name`` is ``SPT_Runtime`` or ``SPT``, depending on the
    detected layout of the installation.
    """
    # Deviates from the verbatim patterns of HANDOFF §5: "!dir/" only
    # re-includes the directory entry itself, while the leading "*" keeps
    # matching every file basename at any depth. The "!dir/**" and
    # "!dir/*.json" lines re-include the contents that have to stay trackable.
    # "BepInEx/patchers/" goes beyond §5 as well: mods such as WTT-CommonLib
    # ship patcher DLLs that must be tracked like plugins.
    return f"""\
*
!.gitignore
!{runtime_dir_name}/
{runtime_dir_name}/*
!{runtime_dir_name}/user/
{runtime_dir_name}/user/*
!{runtime_dir_name}/user/mods/
!{runtime_dir_name}/user/mods/**
!{runtime_dir_name}/user/profiles/
!{runtime_dir_name}/user/profiles/*.json
{runtime_dir_name}/user/profiles/backups/
!BepInEx/
BepInEx/*
!BepInEx/plugins/
!BepInEx/plugins/**
!BepInEx/config/
!BepInEx/config/**
!BepInEx/patchers/
!BepInEx/patchers/**
"""


COMMITTER_NAME: Final = "sptmod"
COMMITTER_EMAIL: Final = "sptmod@localhost"

_CONFIG_KEY_UNSET: Final = 1
_RECORD_SEPARATOR: Final = "\x1e"
_FIELD_SEPARATOR: Final = "\x1f"
_TRAILER_SEPARATOR: Final = "\x1d"
_LOG_FORMAT: Final = "%H%x1f%s%x1f%cI%x1f%(trailers:unfold,separator=%x1d)%x1e"
_FILE_CHANGE_BY_STATUS: Final[dict[str, FileChange]] = {"A": "added", "D": "deleted"}


def _parse_trailers(block: str) -> dict[str, str]:
    """Parse git trailers from a block of text.

    Each line is split on the first colon. Leading/trailing whitespace is
    stripped from keys and values.

    Args:
        block: Trailer block text with lines separated by ``_TRAILER_SEPARATOR``.

    Returns:
        Dictionary of trailer key-value pairs.
    """
    trailers: dict[str, str] = {}
    for line in block.split(_TRAILER_SEPARATOR):
        key, separator, value = line.partition(":")
        if not separator:
            continue
        trailers[key.strip()] = value.strip()
    return trailers


def _reject_multiline_trailers(trailers: Mapping[str, str] | None) -> None:
    """Validate that trailer keys and values contain no newlines.

    Args:
        trailers: Trailer key-value pairs to validate.

    Raises:
        GitError: Any trailer key or value contains ``\n`` or ``\r``.
    """
    if not trailers:
        return
    for key, value in trailers.items():
        if any(character in key or character in value for character in ("\n", "\r")):
            raise GitError(f"trailer {key!r} must not contain line breaks")


def _matches_filter(trailers: Mapping[str, str], wanted: Mapping[str, str]) -> bool:
    """Check if trailers match all key-value pairs (case-insensitive).

    Args:
        trailers: Trailers to check against.
        wanted: Required key-value pairs.

    Returns:
        ``True`` if trailers contain all wanted keys with matching values
        (case-insensitive).
    """
    return all(
        key in trailers and trailers[key].casefold() == value.casefold()
        for key, value in wanted.items()
    )


class Repo:
    """A game directory tracked as a git repository."""

    def __init__(self, path: Path) -> None:
        """Initialize a repository handle for the given path.

        Args:
            path: The root directory of the repository.
        """
        self.path = path

    @classmethod
    def init(cls, layout: "GameLayout") -> "Repo":
        """Create a repository in ``layout.root`` with the whitelist ``.gitignore``.

        Writes the whitelist for the detected runtime directory and sets a local
        committer identity so operations do not depend on the global git config.
        The repository has no commit yet; the initial commit is made through
        :meth:`commit`.

        Raises:
            NotAGameDirError: ``layout.root`` is no directory.
            GitError: the directory is already a repository or git failed.
        """
        path = layout.root
        if not path.is_dir():
            raise NotAGameDirError(f"{path} is not a directory")
        if (path / ".git").exists():
            raise GitError(f"{path} is already a git repository")

        repo = cls(path)
        repo._git("init", "--quiet")
        repo.configure(layout)
        return repo

    def configure(self, layout: "GameLayout") -> None:
        """Write the whitelist ``.gitignore`` if absent and set the identity.

        The local committer identity keeps operations independent of the global
        git config. Safe to call on a repository that is already configured,
        which is how a half-finished init is resumed.

        Raises:
            GitError: git failed.
        """
        gitignore = self.path / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(
                gitignore_whitelist(layout.runtime_dir_name), encoding="utf-8"
            )
        self._git("config", "user.name", COMMITTER_NAME)
        self._git("config", "user.email", COMMITTER_EMAIL)

    def commit(
        self,
        message: str,
        trailers: Mapping[str, str] | None = None,
        paths: Sequence[Path] | None = None,
    ) -> str:
        """Stage ``paths`` and commit, or stage everything the whitelist allows.

        If ``paths`` is ``None``, stages all files matching the whitelist before
        committing. If ``paths`` is given, stages only those paths (not the full
        whitelist). Trailers are appended with ``git interpret-trailers``. Empty
        values are kept as an empty trailer. Returns the hash of the new commit.

        Args:
            message: The commit message.
            trailers: Commit trailers as key-value pairs (optional).
            paths: Explicit paths to stage. If ``None``, uses whitelist (optional).

        Raises:
            GitError: nothing to commit, ``paths`` is an empty sequence, trailer
                values contain newlines, or git failed.
        """
        if paths is not None and not paths:
            raise GitError("nothing to commit: no paths given")
        _reject_multiline_trailers(trailers)

        args = ["commit", "--cleanup=whitespace", "--file=-"]
        if paths is None:
            self._stage_whitelisted()
        else:
            pathspec = [path.as_posix() for path in paths]
            self._git("add", "--", *pathspec)
            args.extend(["--", *pathspec])

        result = self._run(*args, stdin=self._with_trailers(message, trailers))
        if result.returncode != 0:
            raise GitError(f"commit failed: {_failure_detail(result)}")
        return self._head()

    def revert(
        self, commit_hash: str, extra_trailers: Mapping[str, str] | None = None
    ) -> str:
        """Revert ``commit_hash`` and add ``extra_trailers`` to the new commit.

        On conflict, the revert is aborted, the tree is left with no staged or
        conflicting file changes (but may retain untracked files), and the error
        carries the conflicting paths. Untracked files are never staged or
        absorbed. Returns the hash of the revert commit.

        Args:
            commit_hash: The commit to revert.
            extra_trailers: Trailers to add to the revert commit (optional).

        Raises:
            GitError: the revert conflicted (abort-failed or not), trailer values
                contain newlines, or git failed.
        """
        return self._sequencer("revert", commit_hash, extra_trailers)

    def cherry_pick(
        self, commit_hash: str, extra_trailers: Mapping[str, str] | None = None
    ) -> str:
        """Cherry-pick ``commit_hash`` and add ``extra_trailers``.

        Shares the conflict and untracked-file contract with :meth:`revert`.
        Returns the hash of the new commit.

        Args:
            commit_hash: The commit to cherry-pick.
            extra_trailers: Trailers to add to the cherry-pick commit (optional).

        Raises:
            GitError: the cherry-pick conflicted, trailer values contain newlines,
                or git failed.
        """
        return self._sequencer("cherry-pick", commit_hash, extra_trailers)

    def log(self, trailer_filter: Mapping[str, str] | None = None) -> list[CommitInfo]:
        """Return commits newest first, with their trailers parsed.

        If the repository has no commits yet, returns an empty list. The
        ``trailer_filter`` keeps only commits whose trailers contain all given
        key/value pairs; values are compared case-insensitively.

        Args:
            trailer_filter: Only return commits matching these trailer key/values
                (case-insensitive, optional).

        Returns:
            List of commits with parsed trailers, newest first.

        Raises:
            GitError: git failed.
        """
        if self._run("rev-parse", "--verify", "--quiet", "HEAD").returncode != 0:
            return []

        commits = self._parse_log(self._git("log", f"--format={_LOG_FORMAT}"))
        if not trailer_filter:
            return commits
        return [
            commit
            for commit in commits
            if _matches_filter(commit.trailers, trailer_filter)
        ]

    def show_files(self, commit_hash: str) -> list[ModFile]:
        """Return the files touched by ``commit_hash``.

        Raises:
            GitError: the commit does not exist or git failed.
        """
        output = self._git(
            "show", "--name-status", "--no-renames", "--format=", "-z", commit_hash
        )
        fields = [field for field in output.split("\0") if field]
        return [
            ModFile(
                path=Path(name),
                change=_FILE_CHANGE_BY_STATUS.get(status[0], "modified"),
            )
            for status, name in zip(fields[::2], fields[1::2], strict=True)
        ]

    def show_file_content(self, commit_hash: str, path: Path) -> str:
        """Return the content of ``path`` as of ``commit_hash``.

        Args:
            commit_hash: The commit to retrieve from.
            path: The file path relative to the repository root.

        Returns:
            The file content as a UTF-8 string.

        Raises:
            GitError: the commit or path does not exist, the content is not
                UTF-8 decodable, or git failed.
        """
        try:
            return self._git("show", f"{commit_hash}:{path.as_posix()}")
        except UnicodeDecodeError as error:
            raise GitError(
                f"{path.as_posix()} at {commit_hash} is not UTF-8 text"
            ) from error

    def last_touching(self, path: Path) -> CommitInfo | None:
        """Return the most recent commit touching ``path``, or ``None``.

        Raises:
            GitError: git failed.
        """
        commits = self._parse_log(
            self._git("log", "-1", f"--format={_LOG_FORMAT}", "--", path.as_posix())
        )
        return commits[0] if commits else None

    def status(self) -> RepoStatus:
        """Return the parsed porcelain status of the working tree and index.

        Reports both working tree changes (modified) and staged but uncommitted
        changes in the index (also in modified). Untracked files are listed
        separately.

        Returns:
            A ``RepoStatus`` with modified (working tree and index) and untracked
            paths.

        Raises:
            GitError: git failed.
        """
        output = self._git(
            "status", "--porcelain", "--untracked-files=all", "--no-renames", "-z"
        )
        modified: list[Path] = []
        untracked: list[Path] = []
        for entry in output.split("\0"):
            if not entry:
                continue
            code, path = entry[:2], Path(entry[3:])
            if code == "??":
                untracked.append(path)
            elif code != "  ":
                modified.append(path)
        return RepoStatus(modified=modified, untracked=untracked)

    def is_clean(self) -> bool:
        """Return whether the working tree and index have no changes.

        Returns ``False`` if there are modified (working tree or staged) files
        or untracked files.

        Returns:
            ``True`` if the tree has no changes, ``False`` otherwise.

        Raises:
            GitError: git failed.
        """
        status = self.status()
        return not status.modified and not status.untracked

    def config_get(self, key: str) -> str | None:
        """Return the local git config value of ``key``, or ``None`` if unset.

        Args:
            key: The config key in ``section.name`` form.

        Raises:
            GitError: git failed for a reason other than the key being unset.
        """
        result = self._run("config", "--local", "--get", key)
        if result.returncode == _CONFIG_KEY_UNSET:
            return None
        if result.returncode != 0:
            raise GitError(f"git config --get {key} failed: {_failure_detail(result)}")
        return result.stdout.strip()

    def config_set(self, key: str, value: str) -> None:
        """Set the local git config ``key`` to ``value``.

        Args:
            key: The config key in ``section.name`` form.
            value: The value to store; replaces an existing one.

        Raises:
            GitError: git failed.
        """
        self._git("config", "--local", key, value)

    def untracked_under(self, prefix: Path) -> list[Path]:
        """Return untracked, non-ignored paths below ``prefix``.

        Raises:
            GitError: git failed.
        """
        output = self._git(
            "ls-files", "--others", "--exclude-standard", "-z", "--", prefix.as_posix()
        )
        return [Path(entry) for entry in output.split("\0") if entry]

    def _sequencer(
        self,
        command: str,
        commit_hash: str,
        extra_trailers: Mapping[str, str] | None,
    ) -> str:
        """Execute a sequencer operation (revert or cherry-pick) with optional trailers.

        On conflict, aborts the operation and raises GitError with conflicting paths.
        On success, amends trailers if provided.

        Args:
            command: The sequencer command ("revert" or "cherry-pick").
            commit_hash: The commit to apply.
            extra_trailers: Trailers to add to the resulting commit (optional).

        Returns:
            The hash of the resulting commit.

        Raises:
            GitError: Conflict, trailer validation failure, or
                git failure.
        """
        _reject_multiline_trailers(extra_trailers)

        result = self._run(command, "--no-edit", commit_hash)
        if result.returncode != 0:
            conflicting = self._conflicting_paths()
            abort = self._run(command, "--abort")
            if abort.returncode != 0:
                raise GitError(
                    f"{command} of {commit_hash} conflicted and the abort failed: "
                    f"{_failure_detail(abort)}",
                    conflicting,
                )
            raise GitError(
                f"{command} of {commit_hash} failed: {_failure_detail(result)}",
                conflicting,
            )

        self._amend_trailers(extra_trailers)
        return self._head()

    def _amend_trailers(self, trailers: Mapping[str, str] | None) -> None:
        """Amend the HEAD commit to add trailers to its message.

        Args:
            trailers: Trailers to add (optional). Does nothing if ``None`` or empty.

        Raises:
            GitError: git failed.
        """
        if not trailers:
            return
        message = self._with_trailers(self._git("log", "-1", "--format=%B"), trailers)
        self._git(
            "commit", "--amend", "--cleanup=whitespace", "--file=-", stdin=message
        )

    def _conflicting_paths(self) -> list[Path]:
        """Return paths with unmerged changes (conflicts).

        Returns:
            List of conflicted file paths.

        Raises:
            GitError: git failed.
        """
        output = self._git("diff", "--name-only", "--diff-filter=U", "-z")
        return [Path(entry) for entry in output.split("\0") if entry]

    def _with_trailers(self, message: str, trailers: Mapping[str, str] | None) -> str:
        """Append trailers to a message using ``git interpret-trailers``.

        Args:
            message: The base message.
            trailers: Trailers to append (optional).

        Returns:
            The message with trailers appended.

        Raises:
            GitError: git failed.
        """
        if not trailers:
            return message
        args = ["interpret-trailers", "--if-exists", "add"]
        for key, value in trailers.items():
            args.extend(["--trailer", f"{key}: {value}"])
        return self._git(*args, stdin=message)

    def _parse_log(self, output: str) -> list[CommitInfo]:
        """Parse raw git log output into CommitInfo objects.

        Expected format: records separated by ``_RECORD_SEPARATOR``, fields
        within each record separated by ``_FIELD_SEPARATOR``. Expects 4 fields
        per record: hash, subject, ISO date, and trailer block.

        Args:
            output: Raw log output from ``git log --format=_LOG_FORMAT``.

        Returns:
            List of parsed commits.
        """
        commits: list[CommitInfo] = []
        for record in output.split(_RECORD_SEPARATOR):
            fields = record.strip("\n").split(_FIELD_SEPARATOR)
            if len(fields) != 4:
                continue
            commit_hash, subject, date, trailer_block = fields
            commits.append(
                CommitInfo(
                    hash=commit_hash,
                    subject=subject,
                    date=datetime.fromisoformat(date),
                    trailers=_parse_trailers(trailer_block),
                )
            )
        return commits

    def _stage_whitelisted(self) -> None:
        """Stage all whitelisted changes in the working tree.

        Raises:
            GitError: git failed.
        """
        self._git("add", "--all", "--", ".")

    def _head(self) -> str:
        """Get the hash of the HEAD commit.

        Returns:
            The commit hash.

        Raises:
            GitError: git failed.
        """
        return self._git("rev-parse", "HEAD").strip()

    def _git(self, *args: str, stdin: str | None = None) -> str:
        """Run a git command and return stdout, or raise GitError on failure.

        Args:
            *args: Git command arguments.
            stdin: Standard input for the command (optional).

        Returns:
            The stdout output.

        Raises:
            GitError: The command failed (non-zero exit code).
        """
        result = self._run(*args, stdin=stdin)
        if result.returncode != 0:
            raise GitError(f"git {' '.join(args)} failed: {_failure_detail(result)}")
        return result.stdout

    def _run(
        self, *args: str, stdin: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run a git command in this repository and return the result.

        Args:
            *args: Git command arguments.
            stdin: Standard input for the command (optional).

        Returns:
            The completed process result (does not raise on non-zero exit).
        """
        return subprocess.run(
            ["git", "-C", str(self.path), *args],
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )


def _failure_detail(result: subprocess.CompletedProcess[str]) -> str:
    """Extract error message from a failed subprocess result.

    Prefers stderr; falls back to stdout if stderr is empty.

    Args:
        result: The completed process result.

    Returns:
        The error message string, stripped of leading/trailing whitespace.
    """
    return result.stderr.strip() or result.stdout.strip()
