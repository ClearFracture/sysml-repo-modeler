from __future__ import annotations

import tarfile
from pathlib import Path

MAX_ARCHIVE_MEMBERS = 10_000
MAX_MEMBER_SIZE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_SIZE_BYTES = 1024 * 1024 * 1024


class UnsafeTarMemberError(ValueError):
    pass


def safe_extract_tar(archive: tarfile.TarFile, dest: Path) -> None:
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)

    total_size = 0
    member_count = 0

    for member in archive.getmembers():
        member_count += 1
        if member_count > MAX_ARCHIVE_MEMBERS:
            raise UnsafeTarMemberError(
                f"Archive contains more than {MAX_ARCHIVE_MEMBERS} members."
            )

        if member.issym() or member.islnk() or member.isdev():
            raise UnsafeTarMemberError(
                f"Archive member {member.name!r} uses unsupported link or device type."
            )

        if member.size > MAX_MEMBER_SIZE_BYTES:
            raise UnsafeTarMemberError(
                f"Archive member {member.name!r} exceeds the maximum allowed size."
            )

        total_size += member.size
        if total_size > MAX_TOTAL_SIZE_BYTES:
            raise UnsafeTarMemberError("Archive exceeds the maximum allowed total size.")

        target = _safe_member_path(dest, member.name)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        extracted = archive.extractfile(member)
        if extracted is None:
            raise UnsafeTarMemberError(
                f"Archive member {member.name!r} could not be extracted."
            )
        with extracted as source, target.open("wb") as handle:
            handle.write(source.read())


def _safe_member_path(dest: Path, member_name: str) -> Path:
    if member_name.startswith("/") or member_name.startswith("\\"):
        raise UnsafeTarMemberError(
            f"Archive member {member_name!r} uses an absolute path."
        )
    if Path(member_name).is_absolute():
        raise UnsafeTarMemberError(
            f"Archive member {member_name!r} uses an absolute path."
        )

    normalized = member_name.replace("\\", "/").lstrip("/")
    if not normalized or normalized.endswith("/"):
        normalized = normalized.rstrip("/")
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    if ".." in parts:
        raise UnsafeTarMemberError(
            f"Archive member {member_name!r} escapes the extraction directory."
        )

    target = dest.joinpath(*parts).resolve()
    try:
        target.relative_to(dest)
    except ValueError as error:
        raise UnsafeTarMemberError(
            f"Archive member {member_name!r} escapes the extraction directory."
        ) from error
    return target
