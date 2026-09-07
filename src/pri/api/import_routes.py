"""HTTP routes for the import pipeline.

Eight routes: fetch the template, list and fetch the samples, upload, review,
commit, reject, and export a live production back out.

Every filename that reaches a ``Content-Disposition`` header is sanitised, and
every error carries the full typed issue list in the body — the review screen
renders those records directly, so the shape is part of the contract rather
than a debugging convenience.
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import Response

from pri.api.deps import RepoDep, SettingsDep, require_api_key
from pri.importer.limits import ACCEPTED_EXTENSIONS, MAX_UPLOAD_BYTES, sanitise_filename
from pri.importer.samples import SAMPLE_NAMES, SAMPLES_DIR
from pri.importer.service import ImportNotFoundError, ImportReport, ImportService
from pri.importer.spec import TEMPLATE_VERSION, WORKBOOK_SPEC
from pri.importer.template import build_template, export_state

__all__ = ["router"]

_log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api", tags=["import"])
guarded = [Depends(require_api_key)]

_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: Read in bounded chunks so a client claiming a small Content-Length and then
#: streaming gigabytes is cut off at the limit rather than after it.
_CHUNK = 1024 * 1024


def _attachment(filename: str) -> dict[str, str]:
    """A Content-Disposition header that cannot be used to inject one."""
    safe = sanitise_filename(filename).replace(";", "_")
    return {"Content-Disposition": f'attachment; filename="{safe}"'}


# ---------------------------------------------------------------------------
# Template and samples
# ---------------------------------------------------------------------------


@router.get("/import/template")
async def get_template() -> Response:
    """Stream the blank import template."""
    return Response(
        content=build_template(),
        media_type=_XLSX_MEDIA,
        headers=_attachment(f"pri-import-template-v{TEMPLATE_VERSION}.xlsx"),
    )


@router.get("/import/samples")
async def list_samples() -> dict[str, Any]:
    """List the sample workbooks a first-time user can try.

    Reports each sample's byte size, or ``None`` when it has not been generated
    — better than a 404 at download time from a link that looked live.
    """
    samples = []
    for name, description in SAMPLE_NAMES.items():
        path = SAMPLES_DIR / f"{name}.xlsx"
        samples.append(
            {
                "name": name,
                "description": description,
                "filename": f"{name}.xlsx",
                "bytes": path.stat().st_size if path.exists() else None,
                "available": path.exists(),
            }
        )
    return {"template_version": TEMPLATE_VERSION, "samples": samples}


@router.get("/import/samples/{name}")
async def get_sample(name: str) -> Response:
    """Stream one sample workbook.

    Failure modes:
        404 for a name that is not one of the known samples. The name is
        matched against a fixed dictionary rather than joined onto a path, so
        ``../../etc/passwd`` is a miss, not a traversal.
    """
    if name not in SAMPLE_NAMES:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No sample named {name!r}. Available: {', '.join(SAMPLE_NAMES)}",
        )
    path = SAMPLES_DIR / f"{name}.xlsx"
    if not path.exists():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"The {name!r} sample has not been generated. Run: python -m pri.importer.samples",
        )
    return Response(
        content=path.read_bytes(),
        media_type=_XLSX_MEDIA,
        headers=_attachment(f"{name}.xlsx"),
    )


# ---------------------------------------------------------------------------
# Upload and review
# ---------------------------------------------------------------------------


@router.post(
    "/import",
    response_model=ImportReport,
    dependencies=guarded,
    status_code=status.HTTP_200_OK,
)
async def upload(
    request: Request,
    repo: RepoDep,
    file: Annotated[UploadFile, File(description="An .xlsx, .xlsm or .zip of CSVs")],
    uploaded_by: Annotated[str, Form()] = "unknown",
) -> ImportReport:
    """Upload a production workbook and stage it for review.

    Returns 200 with the report even when the workbook is unusable — a file
    that failed to parse is a normal outcome with a typed error list, not an
    HTTP error. 413 is reserved for a file too large to read at all.

    Failure modes:
        413 when the upload exceeds the size limit.
        422 when the multipart body carries no file.
    """
    del request
    payload = await _read_capped(file)
    if not payload:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "No file was uploaded.",
        )

    service = ImportService(repo)
    report = await service.stage_import(payload, file.filename or "upload.xlsx", uploaded_by)
    _log.info(
        "import_upload",
        staging_id=report.staging_id,
        status=report.status,
        errors=len(report.errors),
        violations=len(report.existing_violations),
    )
    return report


async def _read_capped(file: UploadFile) -> bytes:
    """Read an upload, refusing it the moment it passes the limit.

    Reading ``await file.read()`` unbounded would buffer whatever the client
    sends before anyone checks the size, which is the same as having no limit.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"The file is larger than the {MAX_UPLOAD_BYTES // 1_048_576} MB limit.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("/import/{staging_id}", response_model=ImportReport)
async def get_import(staging_id: str, repo: RepoDep) -> ImportReport:
    """Fetch a staged import's report.

    Failure modes:
        404 for an unknown id.
    """
    try:
        return await ImportService(repo).get_import(staging_id)
    except ImportNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/import/{staging_id}/commit", dependencies=guarded)
async def commit_import(staging_id: str, body: dict[str, Any], repo: RepoDep) -> dict[str, Any]:
    """Commit a staged import as version 1 of a new production.

    Idempotent: a second call returns the same version and writes no second
    state row.

    Failure modes:
        404 for an unknown id; 409 for a row that is not awaiting review.
    """
    confirmed_by = str(body.get("confirmed_by") or "").strip()
    if not confirmed_by:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "confirmed_by is required — an import is committed by a person, and the "
            "audit record names them.",
        )

    service = ImportService(repo)
    try:
        production_id, version = await service.commit_import(staging_id, confirmed_by)
    except ImportNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    return {"production_id": production_id, "version": version}


@router.post("/import/{staging_id}/reject", dependencies=guarded)
async def reject_import(staging_id: str, body: dict[str, Any], repo: RepoDep) -> dict[str, Any]:
    """Discard a staged import, keeping the reason on the record."""
    reason = str(body.get("reason") or "").strip() or "No reason given."
    rejected_by = str(body.get("rejected_by") or "unknown")
    try:
        await ImportService(repo).reject_import(staging_id, reason, rejected_by)
    except ImportNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return {"staging_id": staging_id, "status": "REJECTED", "reason": reason}


@router.post("/import/maintenance/purge", dependencies=guarded)
async def purge_expired(repo: RepoDep) -> dict[str, int]:
    """Delete pending reviews past their 24-hour expiry."""
    return {"removed": await ImportService(repo).purge_expired()}


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


@router.get("/productions/{production_id}/export")
async def export_production(
    production_id: str,
    repo: RepoDep,
    settings: SettingsDep,
    version: int | None = None,
) -> Response:
    """Export a production's current state as an import-shaped workbook.

    The output is a valid input: re-uploading it (under a new
    ``production_id``) round-trips to the same state.

    Failure modes:
        404 when the production or version does not exist.
        422 when the state cannot be expressed in the workbook format — a
        location whose permit windows follow more than one daily pattern, for
        instance, which the format has no way to write down.
    """
    del settings
    state = (
        await repo.get_state(production_id, version)
        if version is not None
        else await repo.get_current_state(production_id)
    )
    try:
        payload = export_state(state)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    return Response(
        content=payload,
        media_type=_XLSX_MEDIA,
        headers=_attachment(f"{production_id}-v{state.version}.xlsx"),
    )


@router.get("/import/spec/sheets")
async def describe_spec() -> dict[str, Any]:
    """The workbook spec, as data.

    Lets the upload screen name the seven sheets, and their columns, without
    duplicating the spec in TypeScript — the one place a column is defined
    stays the one place it is defined.
    """
    return {
        "template_version": TEMPLATE_VERSION,
        "max_upload_bytes": MAX_UPLOAD_BYTES,
        "accepted_extensions": list(ACCEPTED_EXTENSIONS),
        "sheets": [
            {
                "name": sheet.name,
                "required": sheet.required,
                "help": sheet.help,
                "max_rows": sheet.max_rows,
                "columns": [
                    {
                        "name": column.name,
                        "kind": str(column.kind),
                        "required": column.required,
                        "format": column.format_hint,
                        "help": column.help,
                    }
                    for column in sheet.columns
                ],
            }
            for sheet in WORKBOOK_SPEC
        ],
    }
