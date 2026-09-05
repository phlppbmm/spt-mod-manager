"""Minimal local stub for the untyped ``rarfile`` package.

rarfile ships no ``py.typed`` marker and no stub package exists on PyPI. This
stub covers only the API ``sptmod.core.archive`` and its tests use.
"""

from pathlib import Path
from types import TracebackType
from typing import Self

class Error(Exception): ...
class RarExecError(Error): ...
class RarCannotExec(RarExecError): ...

class RarFile:
    def __init__(self, file: str | Path) -> None: ...
    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
    def namelist(self) -> list[str]: ...
    def extractall(self, path: str | Path | None = None) -> None: ...

def tool_setup(
    unrar: bool = True,
    unar: bool = True,
    bsdtar: bool = True,
    sevenzip: bool = True,
    sevenzip2: bool = True,
    force: bool = False,
) -> object: ...
