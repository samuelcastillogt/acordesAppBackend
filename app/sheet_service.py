from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

from .db_models import SourceRecord


class SheetUnavailableError(RuntimeError):
    """Raised when a published sheet cannot be read or verified."""


def read_verified_sheet(data_dir: Path, source: SourceRecord) -> str:
    relative_path = PurePosixPath(source.source_file)
    if (
        relative_path.is_absolute()
        or ".." in relative_path.parts
        or len(relative_path.parts) != 2
        or relative_path.suffix != ".txt"
        or relative_path.parts[0] != source.source_artist_slug
    ):
        raise SheetUnavailableError("unsafe_source_path")

    if data_dir.is_symlink():
        raise SheetUnavailableError("source_symlink_rejected")

    path = data_dir
    for part in relative_path.parts:
        path /= part
        if path.is_symlink():
            raise SheetUnavailableError("source_symlink_rejected")

    try:
        resolved_root = data_dir.resolve(strict=True)
        resolved = path.resolve(strict=True)
        resolved.relative_to(resolved_root)
        text = resolved.read_bytes().decode("utf-8", errors="strict")
    except (OSError, UnicodeError, ValueError) as error:
        raise SheetUnavailableError("source_file_unavailable") from error

    _, separator, body_with_newline = text.partition("\n\n")
    if not separator:
        raise SheetUnavailableError("invalid_source_wrapper")

    body = body_with_newline[:-1] if body_with_newline.endswith("\n") else body_with_newline
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if digest != source.content_hash:
        raise SheetUnavailableError("source_integrity_mismatch")
    return body
