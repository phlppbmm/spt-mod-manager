"""Minimal local stub for the untyped ``libarchive-c`` package.

libarchive-c ships no ``py.typed`` marker and no stub package exists on PyPI.
This stub covers only the API ``sptmod.core.archive`` uses to read the 7z
archives py7zr cannot decode.
"""

from collections.abc import Iterator
from contextlib import AbstractContextManager

class ArchiveError(Exception): ...

class ArchiveEntry:
    @property
    def pathname(self) -> str: ...
    @property
    def isdir(self) -> bool: ...
    @property
    def mode(self) -> int: ...
    def get_blocks(self, block_size: int = ...) -> Iterator[bytes]: ...

class ArchiveRead:
    def __iter__(self) -> Iterator[ArchiveEntry]: ...

def file_reader(
    path: str,
    format_name: str = ...,
    filter_name: str = ...,
    block_size: int = ...,
    passphrase: str | None = ...,
    header_codec: str = ...,
) -> AbstractContextManager[ArchiveRead]: ...
