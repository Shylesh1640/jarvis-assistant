"""Routes for IDE integration (Phase 8).

* ``POST /ide/execute-command?confirm=1`` — run a command in the workspace
* ``POST /ide/open-file?confirm=1``       — read a workspace file (capped)
* ``POST /ide/search-files?confirm=1``    — glob files in the workspace
* ``POST /ide/run-tests?confirm=1``       — run pytest in the workspace

Every endpoint requires ``?confirm=1`` (explicit human action) and is
confined to ``IDE_WORKSPACE_ROOT`` — paths that escape the root are rejected.
With the feature disabled or unconfigured, routes return a structured
``503 ide_not_configured`` response and never touch the filesystem.
"""
from __future__ import annotations

from fastapi import APIRouter

from jarvis.api.errors import APIError
from jarvis.api.schemas.ide import IdeCommand, IdeOpenFile, IdeRunTests, IdeSearchFiles
from jarvis.config.settings import settings
from jarvis.ide import (
    IDEUnconfiguredError,
    OutsideWorkspaceError,
    execute_command,
    open_file,
    run_tests,
    search_files,
)
from jarvis.security.session_auth import ensure_session_context

router = APIRouter(prefix="/ide", tags=["ide"])


def _require_ide() -> None:
    if not settings.ide_integration_enabled or not settings.ide_workspace_root:
        raise APIError(
            503,
            "ide_not_configured",
            "IDE integration is not configured: set IDE_INTEGRATION_ENABLED=true "
            "and IDE_WORKSPACE_ROOT to an existing directory.",
        )


def _confirm_or_400(confirm: bool, what: str) -> None:
    if not confirm:
        raise APIError(
            400,
            "confirmation_required",
            f"Pass ?confirm=1 to {what}.",
        )


def _workspace_errors(exc: Exception) -> APIError:
    if isinstance(exc, OutsideWorkspaceError):
        return APIError(403, "outside_workspace", str(exc))
    if isinstance(exc, IDEUnconfiguredError):
        return APIError(503, "ide_not_configured", str(exc))
    return APIError(400, "ide_error", str(exc))


@router.post("/execute-command")
def ide_execute_command(payload: IdeCommand, confirm: bool = False) -> dict:
    ensure_session_context("default", None)
    _require_ide()
    _confirm_or_400(confirm, "execute this command")
    try:
        return execute_command(payload.command)
    except Exception as exc:  # noqa: BLE001
        raise _workspace_errors(exc) from exc


@router.post("/open-file")
def ide_open_file(payload: IdeOpenFile, confirm: bool = False) -> dict:
    ensure_session_context("default", None)
    _require_ide()
    _confirm_or_400(confirm, "open this file")
    try:
        return open_file(payload.path)
    except Exception as exc:  # noqa: BLE001
        raise _workspace_errors(exc) from exc


@router.post("/search-files")
def ide_search_files(payload: IdeSearchFiles, confirm: bool = False) -> dict:
    ensure_session_context("default", None)
    _require_ide()
    _confirm_or_400(confirm, "search the workspace")
    try:
        return search_files(payload.pattern, payload.path)
    except Exception as exc:  # noqa: BLE001
        raise _workspace_errors(exc) from exc


@router.post("/run-tests")
def ide_run_tests(payload: IdeRunTests, confirm: bool = False) -> dict:
    del payload
    ensure_session_context("default", None)
    _require_ide()
    _confirm_or_400(confirm, "run the tests")
    try:
        return run_tests()
    except Exception as exc:  # noqa: BLE001
        raise _workspace_errors(exc) from exc