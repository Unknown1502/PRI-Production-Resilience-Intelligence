"""Limits and hardening for uploaded files.

Nothing from an uploaded file is trusted: not the filename, not a sheet name,
not a cell. This module holds the checks that run *before* a single cell is
read, plus the zip guards, because the cheapest place to reject a hostile file
is at the door.

The threats being defended against, in the order they are checked:

``too large``          a 2 GB upload that exhausts the request body buffer
``wrong type``         a renamed executable, or an HTML error page saved as .xlsx
``zip slip``           an archive entry named ``../../etc/passwd``
``zip bomb``           a 40 KB archive that decompresses to 4 GB
``XML entity attacks`` billion laughs and external-entity expansion in the
                       sheet XML, which openpyxl parses
``slow parse``         a file that is valid but takes four minutes, holding a
                       worker open
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO

__all__ = [
    "ACCEPTED_EXTENSIONS",
    "MAX_ENTRY_COUNT",
    "MAX_PARSE_SECONDS",
    "MAX_UNCOMPRESSED_BYTES",
    "MAX_UPLOAD_BYTES",
    "MAX_ZIP_RATIO",
    "FileKind",
    "LimitExceeded",
    "assert_safe_xml",
    "detect_kind",
    "safe_zip_entries",
    "sanitise_filename",
]

#: 10 MB. A 500-row schedule with 2,000 scenes is well under 1 MB; anything an
#: order of magnitude larger is either an image-laden report or an attack.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

#: Total bytes a zip may decompress to.
MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024

#: Compression ratio above which an archive is assumed to be a bomb. Real
#: spreadsheet XML compresses around 10:1; 100:1 is not a spreadsheet.
MAX_ZIP_RATIO = 100

#: A workbook has seven sheets. Thirty-two entries is generous.
MAX_ENTRY_COUNT = 32

#: Wall-clock budget for parsing. Exceeding it fails the import with a clear
#: message rather than holding a worker open indefinitely.
MAX_PARSE_SECONDS = 20.0

#: What the upload control offers and what :func:`detect_kind` will accept.
#: Declared once, served to the browser by the spec route, so the file picker's
#: filter and the server's sniffing cannot drift apart. The extension is a hint
#: for the user's file dialog only — the server still reads the magic bytes.
ACCEPTED_EXTENSIONS: tuple[str, ...] = (".xlsx", ".xlsm", ".zip", ".csv")

#: Magic bytes. xlsx and xlsm are zip archives; a csv has no signature, so it
#: is identified by exclusion and by decoding cleanly.
_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")

#: Legacy .xls (OLE2 compound document). Detected specifically so the error can
#: say "save as .xlsx" rather than "unsupported file".
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class LimitExceeded(ValueError):
    """An upload was refused before parsing.

    Inputs:
        code:     The import error code to report (E016 or E017).
        message:  What is wrong.
        hint:     What the user should do.
    """

    def __init__(self, code: str, message: str, hint: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


class FileKind(StrEnum):
    """What kind of upload this is."""

    XLSX = "XLSX"
    CSV = "CSV"
    CSV_ZIP = "CSV_ZIP"


@dataclass(frozen=True, slots=True)
class ZipEntry:
    """One vetted archive member."""

    name: str
    data: bytes


def sanitise_filename(filename: str) -> str:
    """Reduce a user-supplied filename to something safe to echo and store.

    Strips any directory component, control characters and quotes. The result
    goes into a ``Content-Disposition`` header and a database row, both of
    which are injection surfaces if the name is taken at face value.
    """
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(c for c in name if c.isprintable() and c not in '"\\')
    cleaned = cleaned.strip().strip(".")
    return cleaned[:200] or "upload"


def detect_kind(data: bytes, filename: str) -> FileKind:
    """Identify the upload from its magic bytes, not its extension.

    An extension is a claim by whoever named the file. The first four bytes are
    a fact about its contents, so those decide, and the extension only breaks
    the tie between the two zip-shaped formats.

    Inputs:
        data:     The full upload.
        filename: The sanitised original name, used for the ``.zip`` vs
                  ``.xlsx`` distinction and for the error message.

    Outputs:
        The detected :class:`FileKind`.

    Failure modes:
        Raises :class:`LimitExceeded` with E016 for anything else, naming what
        was uploaded so the user is not left guessing.
    """
    if not data:
        raise LimitExceeded(
            "E016",
            "The uploaded file is empty.",
            "Check the file saved correctly, then upload it again.",
        )

    lowered = filename.lower()

    if data.startswith(_OLE2_MAGIC):
        raise LimitExceeded(
            "E016",
            "This is an old-format .xls workbook.",
            "Open it in Excel and use File → Save As → Excel Workbook (.xlsx), "
            "then upload the .xlsx.",
        )

    if data.startswith(_ZIP_MAGIC):
        if lowered.endswith(".zip"):
            return FileKind.CSV_ZIP
        if lowered.endswith((".xlsx", ".xlsm")):
            return FileKind.XLSX
        # A zip with an unhelpful name: look inside for the workbook marker.
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                names = archive.namelist()
        except zipfile.BadZipFile as exc:
            raise LimitExceeded(
                "E016",
                "The file looks like an archive but could not be opened.",
                "Re-save the workbook as .xlsx and upload it again.",
            ) from exc
        if any(name.startswith("xl/") for name in names):
            return FileKind.XLSX
        return FileKind.CSV_ZIP

    if lowered.endswith(".csv") or _looks_like_text(data):
        return FileKind.CSV

    raise LimitExceeded(
        "E016",
        f"PRI cannot read {filename!r}.",
        "Upload an Excel workbook (.xlsx or .xlsm), or a .zip of CSV files named "
        "after the sheets. Download the template if you need a starting point.",
    )


def _looks_like_text(data: bytes) -> bool:
    """Whether the leading bytes are plausibly a text file.

    Decoding successfully is not enough to conclude anything: cp1252 maps
    almost every byte to *something*, so an ELF binary or a JPEG "decodes"
    cleanly and would be accepted as a CSV. The test that actually
    discriminates is the proportion of control characters — real delimited text
    is overwhelmingly printable, with tab, carriage return and newline the only
    exceptions.
    """
    sample = data[:4096]
    if b"\x00" in sample:
        return False

    decoded: str | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            decoded = sample.decode(encoding)
        except UnicodeDecodeError:
            continue
        else:
            break
    if not decoded:
        return False

    control = sum(1 for ch in decoded if not ch.isprintable() and ch not in "\t\r\n")
    return control / len(decoded) < 0.05


def check_size(data: bytes) -> None:
    """Reject an upload larger than :data:`MAX_UPLOAD_BYTES`.

    Failure modes:
        Raises :class:`LimitExceeded` with E016, naming both the file size and
        the limit — "too large" without the numbers makes the user guess.
    """
    if len(data) > MAX_UPLOAD_BYTES:
        raise LimitExceeded(
            "E016",
            f"The file is {len(data) / 1_048_576:.1f} MB, over the "
            f"{MAX_UPLOAD_BYTES // 1_048_576} MB limit.",
            "Remove embedded images or extra sheets, or split the production, then upload again.",
        )


def safe_zip_entries(data: bytes) -> list[ZipEntry]:
    """Read a zip archive's members after checking it is not hostile.

    Rejects absolute paths and ``..`` traversal, an entry count above
    :data:`MAX_ENTRY_COUNT`, a total uncompressed size above
    :data:`MAX_UNCOMPRESSED_BYTES`, and any single entry whose compression
    ratio exceeds :data:`MAX_ZIP_RATIO`.

    The ratio is checked from the archive's own header *before* reading, so a
    bomb is refused without being decompressed.

    Outputs:
        The vetted entries, directories and empty files excluded.

    Failure modes:
        Raises :class:`LimitExceeded` with E016 for any of the above.
    """
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise LimitExceeded(
            "E016",
            "The archive could not be opened.",
            "Re-create the .zip and upload it again.",
        ) from exc

    with archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]

        if len(infos) > MAX_ENTRY_COUNT:
            raise LimitExceeded(
                "E016",
                f"The archive holds {len(infos)} files, over the limit of {MAX_ENTRY_COUNT}.",
                "Zip only the CSV files for the sheets PRI needs.",
            )

        total = 0
        for info in infos:
            name = info.filename
            parts = name.replace("\\", "/").split("/")
            if name.startswith(("/", "\\")) or ".." in parts:
                raise LimitExceeded(
                    "E016",
                    f"The archive contains an unsafe path: {name!r}.",
                    "Re-create the .zip with the CSV files at the top level.",
                )
            if info.compress_size > 0:
                ratio = info.file_size / info.compress_size
                if ratio > MAX_ZIP_RATIO:
                    raise LimitExceeded(
                        "E016",
                        f"{name!r} decompresses {ratio:.0f} times its stored size.",
                        "This does not look like a spreadsheet. Upload the .xlsx instead.",
                    )
            total += info.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                raise LimitExceeded(
                    "E016",
                    "The archive decompresses to more than "
                    f"{MAX_UNCOMPRESSED_BYTES // 1_048_576} MB.",
                    "Remove anything that is not a CSV of one of PRI's sheets.",
                )

        return [
            ZipEntry(name=info.filename, data=archive.read(info))
            for info in infos
            if info.file_size > 0
        ]


def assert_safe_xml() -> None:
    """Fail loudly at import time if ``defusedxml`` is not installed.

    openpyxl uses ``defusedxml`` when it is present and falls back to the
    standard library parser when it is not. The fallback is vulnerable to
    entity-expansion attacks in a file we are about to parse on a server, and
    the difference is invisible at runtime — so it is asserted rather than
    assumed.

    Failure modes:
        Raises ``RuntimeError`` if the package is missing.
    """
    try:
        import defusedxml  # noqa: F401
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError(
            "defusedxml is not installed. openpyxl would fall back to the standard "
            "library XML parser, which is unsafe for untrusted uploads. "
            "Install it with: pip install defusedxml"
        ) from exc


assert_safe_xml()
