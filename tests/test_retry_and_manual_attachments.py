"""NFR-3 retry/backoff in the SAM.gov client + the FR-1 manual-upload
fallback (files dropped into the attachments dir get picked up)."""

import json
from pathlib import Path

import httpx
import pytest

from bidpilot.intake import (
    register_manual_attachments,
    unprocessed_manual_attachments,
)
from bidpilot.intake.samgov import SamGovClient
from bidpilot.models import AttachmentRecord, NoticeMetadata, NoticePackage


# -- retry / backoff ---------------------------------------------------------


def _client_with(handler) -> tuple[SamGovClient, list[float]]:
    client = SamGovClient(api_key="k")
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    sleeps: list[float] = []
    client._sleep = sleeps.append
    return client, sleeps


def test_retries_5xx_then_succeeds():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) <= 2:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    client, sleeps = _client_with(handler)
    resp = client._get("https://api.sam.gov/x")
    assert resp.json() == {"ok": True}
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]  # exponential backoff


def test_429_honors_retry_after():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "7"})
        return httpx.Response(200, json={})

    client, sleeps = _client_with(handler)
    client._get("https://api.sam.gov/x")
    assert sleeps == [7.0]  # Retry-After outranks the 1s base


def test_404_never_retries():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(404)

    client, sleeps = _client_with(handler)
    with pytest.raises(httpx.HTTPStatusError):
        client._get("https://api.sam.gov/x")
    assert len(calls) == 1 and sleeps == []


def test_exhausted_retries_raises():
    def handler(request):
        return httpx.Response(500)

    client, sleeps = _client_with(handler)
    with pytest.raises(httpx.HTTPStatusError):
        client._get("https://api.sam.gov/x")
    assert len(sleeps) == SamGovClient.MAX_RETRIES


def test_transport_errors_retried():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) <= 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json={})

    client, _ = _client_with(handler)
    assert client._get("https://api.sam.gov/x").status_code == 200
    assert len(calls) == 2


def test_search_goes_through_retry_path(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(502)
        return httpx.Response(200, json={"opportunitiesData": [{"noticeId": "A" * 32}]})

    client, _ = _client_with(handler)
    client.cache_dir = tmp_path
    data = client._search({"noticeid": "A" * 32})
    assert data["opportunitiesData"][0]["noticeId"] == "A" * 32
    assert len(calls) == 2


# -- manual attachment pickup (FR-1 fallback) --------------------------------


def _package(dest: Path) -> NoticePackage:
    return NoticePackage(
        metadata=NoticeMetadata(notice_id="f" * 32),
        files=[
            AttachmentRecord(
                name="restricted_sow.pdf",
                local_path=str(dest / "restricted_sow.pdf"),
                restricted=True,
                source_notice_id="f" * 32,
            )
        ],
        restricted_files_flagged=True,
    )


def test_restricted_file_downloaded_manually_is_unflagged(tmp_path):
    package = _package(tmp_path)
    (tmp_path / "restricted_sow.pdf").write_bytes(b"%PDF-1.4 fake")
    picked = register_manual_attachments(package, tmp_path)
    assert picked == ["restricted_sow.pdf"]
    assert package.files[0].restricted is False
    assert package.files[0].sha256
    assert package.restricted_files_flagged is False


def test_extra_dropped_file_is_registered(tmp_path):
    package = _package(tmp_path)
    (tmp_path / "QA_answers.docx").write_bytes(b"docx bytes")
    picked = register_manual_attachments(package, tmp_path)
    assert "QA_answers.docx" in picked
    names = {f.name for f in package.files}
    assert "QA_answers.docx" in names
    manual = next(f for f in package.files if f.name == "QA_answers.docx")
    assert manual.source_notice_id is None  # manually supplied


def test_sidecar_and_hidden_files_ignored(tmp_path):
    package = _package(tmp_path)
    (tmp_path / ".written.json").write_text("{}")
    picked = register_manual_attachments(package, tmp_path)
    assert picked == []
    assert unprocessed_manual_attachments(package, tmp_path) == []


def test_unprocessed_detection(tmp_path):
    package = NoticePackage(metadata=NoticeMetadata(notice_id="f" * 32))
    (tmp_path / "late_amendment.pdf").write_bytes(b"pdf")
    assert unprocessed_manual_attachments(package, tmp_path) == ["late_amendment.pdf"]
    register_manual_attachments(package, tmp_path)
    assert unprocessed_manual_attachments(package, tmp_path) == []
