#!/usr/bin/env python3
"""Monitor House and Senate periodic transaction reports for configured keywords.

The monitor is deliberately fail-closed: a changed source, unreadable filing, or failed
notification exits non-zero so the scheduler can alert on the failure instead of silently
reporting success.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import os
import re
import sys
import tempfile
import unicodedata
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Sequence
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests import Response, Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    import pdfplumber
except ImportError:  # pragma: no cover - reported clearly at runtime
    pdfplumber = None

try:
    import pytesseract
    from pdf2image import convert_from_bytes, pdfinfo_from_bytes
except ImportError:  # pragma: no cover - reported clearly at runtime
    pytesseract = None
    convert_from_bytes = None
    pdfinfo_from_bytes = None

LOGGER = logging.getLogger("disclosure-monitor")

HOUSE_INDEX_URL = (
    "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.ZIP"
)
HOUSE_PTR_URL = (
    "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"
)
SENATE_ROOT = "https://efdsearch.senate.gov"
SENATE_HOME_URL = f"{SENATE_ROOT}/search/home/"
SENATE_SEARCH_URL = f"{SENATE_ROOT}/search/"
SENATE_REPORTS_URL = f"{SENATE_ROOT}/search/report/data/"
PUSHOVER_MESSAGES_URL = "https://api.pushover.net/1/messages.json"

DEFAULT_KEYWORDS = ("UNH", "UnitedHealth", "UnitedHealth Group")
DEFAULT_STATE_PATH = Path(".monitor-state/disclosures.json")
DEFAULT_RESULT_PATH = Path("monitor-result.json")
DEFAULT_TIMEOUT = (15, 90)
DEFAULT_LOOKBACK_DAYS = 120
DEFAULT_MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_OCR_PAGES = 75
DEFAULT_MAX_SEEN_PER_SOURCE = 25_000
STATE_VERSION = 2


class MonitorError(RuntimeError):
    """Base exception for an operational monitoring failure."""


class SourceChangedError(MonitorError):
    """Raised when a government source no longer has the expected structure."""


class NotificationError(MonitorError):
    """Raised when a positive match could not be delivered."""


@dataclass(frozen=True)
class Report:
    report_id: str
    source: str
    filer: str
    filed_date: str
    url: str
    format: str
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Alert:
    report_id: str
    source: str
    filer: str
    filed_date: str
    url: str
    keywords: tuple[str, ...]
    snippet: str
    details: tuple[str, ...] = ()


@dataclass
class MonitorState:
    version: int = STATE_VERSION
    seen: dict[str, dict[str, str]] = field(
        default_factory=lambda: {"house": {}, "senate": {}}
    )
    last_attempt_utc: str | None = None
    last_success_utc: str | None = None
    last_counts: dict[str, int] = field(default_factory=dict)

    def is_seen(self, source: str, report_id: str) -> bool:
        return report_id in self.seen.setdefault(source, {})

    def mark_seen(self, source: str, report_id: str, timestamp: str) -> None:
        self.seen.setdefault(source, {})[report_id] = timestamp

    def prune(self, max_per_source: int = DEFAULT_MAX_SEEN_PER_SOURCE) -> None:
        for source, values in self.seen.items():
            if len(values) <= max_per_source:
                continue
            ordered = sorted(values.items(), key=lambda item: item[1], reverse=True)
            self.seen[source] = dict(ordered[:max_per_source])


@dataclass(frozen=True)
class Config:
    keywords: tuple[str, ...]
    state_path: Path
    result_path: Path
    source: str
    bootstrap_alerts: bool
    no_notify: bool
    senate_lookback_days: int
    max_download_bytes: int
    max_ocr_pages: int
    user_agent: str
    pushover_api_token: str
    pushover_user_key: str
    require_pushover: bool
    allow_empty_sources: bool
    allow_state_initialization: bool


@dataclass
class RunResult:
    started_utc: str
    finished_utc: str = ""
    source_counts: dict[str, int] = field(default_factory=dict)
    new_counts: dict[str, int] = field(default_factory=dict)
    baseline_counts: dict[str, int] = field(default_factory=dict)
    match_counts: dict[str, int] = field(default_factory=dict)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    success: bool = False


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime | None = None) -> str:
    return (value or utc_now()).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_bool(value: str | bool | None, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off", ""}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


def parse_keywords(raw: str | None) -> tuple[str, ...]:
    values = raw.split(",") if raw else list(DEFAULT_KEYWORDS)
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = value.strip()
        if item and item.casefold() not in seen:
            cleaned.append(item)
            seen.add(item.casefold())
    if not cleaned:
        raise ValueError("At least one keyword must be configured")
    return tuple(cleaned)


def build_session(user_agent: str) -> Session:
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        status=4,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    # A transport-level retry after Pushover accepted a request could duplicate an alert.
    # Keep retries for read-only source POSTs, but not for notification delivery.
    session.mount(PUSHOVER_MESSAGES_URL, HTTPAdapter(max_retries=Retry(total=0)))
    session.headers.update(
        {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/pdf,application/json,*/*;q=0.8",
        }
    )
    return session


def checked_response(response: Response, context: str) -> Response:
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise MonitorError(
            f"{context} returned HTTP {response.status_code}: {response.url}"
        ) from exc
    return response


def response_bytes(
    response: Response,
    context: str,
    max_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
) -> bytes:
    checked_response(response, context)
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise MonitorError(
                    f"{context} is larger than the configured {max_bytes:,}-byte limit"
                )
        except ValueError:
            LOGGER.warning("Invalid Content-Length from %s: %r", response.url, content_length)
    data = response.content
    if len(data) > max_bytes:
        raise MonitorError(
            f"{context} is larger than the configured {max_bytes:,}-byte limit"
        )
    if not data:
        raise MonitorError(f"{context} returned an empty response")
    return data


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", " ", normalized).strip()


def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    escaped = re.escape(keyword)
    # Treat compact all-capital symbols as tickers and require token boundaries.
    if re.fullmatch(r"[A-Z0-9.\-]{1,6}", keyword):
        return re.compile(rf"(?<![A-Z0-9]){escaped}(?![A-Z0-9])", re.IGNORECASE)
    return re.compile(escaped, re.IGNORECASE)


def find_keyword_hits(text: str, keywords: Sequence[str]) -> tuple[str, ...]:
    normalized = normalize_text(text)
    return tuple(keyword for keyword in keywords if _keyword_pattern(keyword).search(normalized))


def text_snippet(text: str, keywords: Sequence[str], radius: int = 180) -> str:
    normalized = normalize_text(text)
    if not normalized:
        return ""
    starts = []
    for keyword in keywords:
        match = _keyword_pattern(keyword).search(normalized)
        if match:
            starts.append(match.start())
    center = min(starts) if starts else 0
    start = max(0, center - radius)
    end = min(len(normalized), center + radius)
    prefix = "…" if start else ""
    suffix = "…" if end < len(normalized) else ""
    return f"{prefix}{normalized[start:end]}{suffix}"


def load_state(path: Path) -> tuple[MonitorState, bool]:
    if not path.exists():
        return MonitorState(), True
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MonitorError(f"State file is unreadable: {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise MonitorError(f"State file must contain a JSON object: {path}")
    version = payload.get("version")
    if version != STATE_VERSION:
        raise MonitorError(
            f"Unsupported state version {version!r} in {path}; expected {STATE_VERSION}"
        )
    seen_payload = payload.get("seen", {})
    if not isinstance(seen_payload, dict):
        raise MonitorError("State field 'seen' must be an object")

    seen: dict[str, dict[str, str]] = {"house": {}, "senate": {}}
    for source in ("house", "senate"):
        source_seen = seen_payload.get(source, {})
        if not isinstance(source_seen, dict):
            raise MonitorError(f"State field seen.{source} must be an object")
        seen[source] = {str(key): str(value) for key, value in source_seen.items()}

    state = MonitorState(
        version=STATE_VERSION,
        seen=seen,
        last_attempt_utc=payload.get("last_attempt_utc"),
        last_success_utc=payload.get("last_success_utc"),
        last_counts={str(k): int(v) for k, v in payload.get("last_counts", {}).items()},
    )
    return state, False


def save_state(path: Path, state: MonitorState) -> None:
    state.prune()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": state.version,
        "seen": state.seen,
        "last_attempt_utc": state.last_attempt_utc,
        "last_success_utc": state.last_success_utc,
        "last_counts": state.last_counts,
    }
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(encoded)
        temp_name = handle.name
    Path(temp_name).replace(path)


def write_result(path: Path, result: RunResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(result), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _clean_row(row: Mapping[str, Any]) -> dict[str, str]:
    return {
        normalize_text(str(key).replace("\ufeff", "")): normalize_text(str(value or ""))
        for key, value in row.items()
        if key is not None
    }


def parse_house_index(zip_bytes: bytes, year: int) -> list[Report]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise SourceChangedError(f"House {year} index is not a valid ZIP file") from exc

    with archive:
        names = archive.namelist()
        expected = [name for name in names if name.lower().endswith(f"{year}fd.txt".lower())]
        candidates = expected or [name for name in names if name.lower().endswith(".txt")]
        if not candidates:
            raise SourceChangedError(
                f"House {year} index contains no tab-delimited TXT file; entries={names!r}"
            )
        raw_text = archive.read(candidates[0])

    text = raw_text.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    rows = [_clean_row(row) for row in reader]
    if not rows:
        raise SourceChangedError(f"House {year} index contains no rows")

    required = {"FilingType", "Year", "FilingDate", "DocID", "Last", "First"}
    missing = required - set(rows[0])
    if missing:
        raise SourceChangedError(
            f"House {year} index is missing expected columns: {sorted(missing)}"
        )

    reports: list[Report] = []
    for row in rows:
        if row.get("FilingType", "").upper() != "P":
            continue
        doc_id = row.get("DocID", "").strip()
        filing_year_text = row.get("Year", "").strip()
        if not doc_id or not filing_year_text.isdigit():
            raise SourceChangedError(
                f"House PTR row is missing a usable Year or DocID: {row!r}"
            )
        filing_year = int(filing_year_text)
        filer = " ".join(
            item
            for item in (
                row.get("Prefix", ""),
                row.get("First", ""),
                row.get("Last", ""),
                row.get("Suffix", ""),
            )
            if item
        )
        reports.append(
            Report(
                report_id=f"house:{filing_year}:{doc_id}",
                source="house",
                filer=filer or "Unknown filer",
                filed_date=row.get("FilingDate", "Unknown"),
                url=HOUSE_PTR_URL.format(year=filing_year, doc_id=doc_id),
                format="pdf",
                metadata={
                    "document_id": doc_id,
                    "district": row.get("StateDst", ""),
                    "filing_year": str(filing_year),
                },
            )
        )
    return reports


def fetch_house_reports(
    session: Session,
    years: Sequence[int],
    max_download_bytes: int,
) -> list[Report]:
    reports: list[Report] = []
    successful_years = 0
    errors: list[str] = []
    for year in years:
        url = HOUSE_INDEX_URL.format(year=year)
        LOGGER.info("Fetching House filing index for %s", year)
        try:
            response = session.get(url, timeout=DEFAULT_TIMEOUT)
            if response.status_code == 404:
                today = utc_now()
                if year == today.year and today.month == 1 and today.day <= 7:
                    LOGGER.warning(
                        "House index is not published yet for the new year %s: %s",
                        year,
                        url,
                    )
                    continue
                raise MonitorError(f"House {year} index returned HTTP 404: {url}")
            data = response_bytes(response, f"House {year} index", max_download_bytes)
            year_reports = parse_house_index(data, year)
            reports.extend(year_reports)
            successful_years += 1
            LOGGER.info("House %s index contains %s PTRs", year, len(year_reports))
        except MonitorError as exc:
            errors.append(str(exc))

    if successful_years == 0:
        raise MonitorError("No House index could be read: " + "; ".join(errors))
    if errors:
        raise MonitorError(
            "One or more required House indexes could not be read: " + "; ".join(errors)
        )
    # Dedupe defensively in case an index contains repeated rows.
    deduped = {report.report_id: report for report in reports}
    return sorted(deduped.values(), key=lambda report: (report.filed_date, report.report_id))


def _cookie_csrf(session: Session) -> str | None:
    for name in ("csrftoken", "csrf"):
        value = session.cookies.get(name)
        if value:
            return value
    return None


def senate_accept_terms(session: Session) -> str:
    response = checked_response(
        session.get(SENATE_HOME_URL, timeout=DEFAULT_TIMEOUT),
        "Senate disclosure landing page",
    )
    soup = BeautifulSoup(response.text, "html.parser")
    input_node = soup.find("input", attrs={"name": "csrfmiddlewaretoken"})
    if input_node and input_node.get("value"):
        form_token = str(input_node["value"])
        accepted = checked_response(
            session.post(
                SENATE_HOME_URL,
                data={
                    "csrfmiddlewaretoken": form_token,
                    "prohibition_agreement": "1",
                },
                headers={"Referer": SENATE_HOME_URL},
                timeout=DEFAULT_TIMEOUT,
            ),
            "Senate disclosure terms acceptance",
        )
        csrf = _cookie_csrf(session)
        if not csrf:
            accepted_soup = BeautifulSoup(accepted.text, "html.parser")
            fallback = accepted_soup.find(
                "input", attrs={"name": "csrfmiddlewaretoken"}
            )
            csrf = str(fallback.get("value")) if fallback and fallback.get("value") else None
    else:
        csrf = _cookie_csrf(session)

    if not csrf:
        raise SourceChangedError(
            "Senate disclosure site did not provide a CSRF token after terms acceptance"
        )
    return csrf


def extract_report_link(link_html: str) -> str:
    soup = BeautifulSoup(link_html, "html.parser")
    anchor = soup.find("a", href=True)
    if not anchor:
        raise SourceChangedError(f"Senate result row has no report link: {link_html!r}")
    return urljoin(SENATE_ROOT, str(anchor["href"]))


def parse_senate_result_rows(rows: Sequence[Sequence[Any]]) -> list[Report]:
    reports: list[Report] = []
    for raw in rows:
        if not isinstance(raw, (list, tuple)) or len(raw) < 5:
            raise SourceChangedError(f"Unexpected Senate result row: {raw!r}")
        first, last, report_type, link_html, date_received = raw[:5]
        url = extract_report_link(str(link_html))
        path = url.lower()
        report_format = "pdf" if "/search/view/paper/" in path or path.endswith(".pdf") else "html"
        filer = normalize_text(f"{first or ''} {last or ''}") or "Unknown filer"
        reports.append(
            Report(
                report_id=f"senate:{url}",
                source="senate",
                filer=filer,
                filed_date=normalize_text(str(date_received or "")) or "Unknown",
                url=url,
                format=report_format,
                metadata={"report_type": normalize_text(str(report_type or ""))},
            )
        )
    return reports


def _senate_payload(
    csrf_token: str,
    offset: int,
    batch_size: int,
    start_date: datetime,
    end_date: datetime,
) -> dict[str, str]:
    return {
        "draw": str(offset // batch_size + 1),
        "start": str(offset),
        "length": str(batch_size),
        "report_types": "[11]",  # Senate's Periodic Transaction Report type.
        "filer_types": "[]",
        "submitted_start_date": start_date.strftime("%m/%d/%Y 00:00:00"),
        "submitted_end_date": end_date.strftime("%m/%d/%Y 23:59:59"),
        "candidate_state": "",
        "senator_state": "",
        "office_id": "",
        "first_name": "",
        "last_name": "",
        "csrfmiddlewaretoken": csrf_token,
    }


def fetch_senate_reports(
    session: Session,
    lookback_days: int,
    now: datetime | None = None,
) -> list[Report]:
    now = now or utc_now()
    start_date = now - timedelta(days=lookback_days)
    csrf = senate_accept_terms(session)
    batch_size = 100
    offset = 0
    rows: list[Sequence[Any]] = []

    for _page in range(100):
        payload = _senate_payload(csrf, offset, batch_size, start_date, now)
        response = checked_response(
            session.post(
                SENATE_REPORTS_URL,
                data=payload,
                headers={
                    "Referer": SENATE_SEARCH_URL,
                    "X-CSRFToken": csrf,
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=DEFAULT_TIMEOUT,
            ),
            "Senate report search",
        )
        try:
            body = response.json()
        except requests.JSONDecodeError as exc:
            excerpt = normalize_text(response.text)[:300]
            raise SourceChangedError(
                f"Senate report search returned non-JSON content: {excerpt!r}"
            ) from exc
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            raise SourceChangedError(
                f"Senate report search JSON is missing a data array: {body!r}"
            )
        batch = body["data"]
        rows.extend(batch)
        if not batch:
            break
        total_raw = body.get("recordsFiltered", body.get("recordsTotal"))
        try:
            total = int(total_raw) if total_raw is not None else None
        except (TypeError, ValueError):
            total = None
        offset += len(batch)
        if len(batch) < batch_size or (total is not None and offset >= total):
            break
    else:
        raise SourceChangedError("Senate report search exceeded 100 result pages")

    reports = parse_senate_result_rows(rows)
    deduped = {report.report_id: report for report in reports}
    LOGGER.info(
        "Senate search returned %s PTRs over the last %s days",
        len(deduped),
        lookback_days,
    )
    return sorted(deduped.values(), key=lambda report: (report.filed_date, report.report_id))


def extract_pdf_text(pdf_bytes: bytes, max_ocr_pages: int) -> str:
    if not pdf_bytes.startswith(b"%PDF"):
        prefix = pdf_bytes[:80].decode("utf-8", errors="replace")
        raise SourceChangedError(f"Expected a PDF but received: {prefix!r}")
    if pdfplumber is None:
        raise MonitorError("pdfplumber is not installed")

    extracted: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                extracted.append(page.extract_text() or "")
    except Exception as exc:
        LOGGER.warning("PDF text extraction failed; trying OCR: %s", exc)

    text = "\n".join(extracted).strip()
    if len(normalize_text(text)) >= 20:
        return text

    if not all((pytesseract, convert_from_bytes, pdfinfo_from_bytes)):
        raise MonitorError(
            "PDF has no usable text layer and OCR dependencies are not installed"
        )

    try:
        info = pdfinfo_from_bytes(pdf_bytes)
        pages = int(info.get("Pages", 0))
    except Exception as exc:
        raise MonitorError(f"Could not determine PDF page count for OCR: {exc}") from exc
    if pages <= 0:
        raise SourceChangedError("PDF reports zero pages")
    if pages > max_ocr_pages:
        raise MonitorError(
            f"PDF has {pages} pages, above OCR_MAX_PAGES={max_ocr_pages}; refusing partial scan"
        )

    ocr_text: list[str] = []
    try:
        for page_number in range(1, pages + 1):
            images = convert_from_bytes(
                pdf_bytes,
                dpi=220,
                first_page=page_number,
                last_page=page_number,
                fmt="jpeg",
                thread_count=1,
            )
            if len(images) != 1:
                raise MonitorError(
                    f"OCR renderer returned {len(images)} images for page {page_number}"
                )
            ocr_text.append(pytesseract.image_to_string(images[0]))
    except MonitorError:
        raise
    except Exception as exc:
        raise MonitorError(f"OCR failed: {exc}") from exc

    text = "\n".join(ocr_text).strip()
    if not normalize_text(text):
        raise MonitorError("PDF extraction and OCR both returned no text")
    return text


def fetch_pdf_bytes(session: Session, url: str, config: Config, context: str) -> bytes:
    response = session.get(url, timeout=DEFAULT_TIMEOUT)
    data = response_bytes(response, context, config.max_download_bytes)
    if data.startswith(b"%PDF"):
        return data

    # Some Senate paper-filing links render an HTML page containing the PDF link.
    content_type = response.headers.get("Content-Type", "").lower()
    if "html" in content_type or data.lstrip().startswith((b"<!DOCTYPE", b"<html", b"<HTML")):
        soup = BeautifulSoup(data, "html.parser")
        link = soup.find("a", href=re.compile(r"\.pdf(?:$|\?)", re.IGNORECASE))
        if link and link.get("href"):
            pdf_url = urljoin(response.url, str(link["href"]))
            pdf_response = session.get(pdf_url, timeout=DEFAULT_TIMEOUT)
            return response_bytes(pdf_response, context, config.max_download_bytes)
    prefix = data[:120].decode("utf-8", errors="replace")
    raise SourceChangedError(f"{context} did not return a PDF: {prefix!r}")


def _senate_page_response(session: Session, report: Report) -> Response:
    response = checked_response(
        session.get(report.url, timeout=DEFAULT_TIMEOUT),
        f"Senate report {report.url}",
    )
    # The site redirects expired sessions back to the terms page.
    if response.url.rstrip("/") == SENATE_HOME_URL.rstrip("/"):
        senate_accept_terms(session)
        response = checked_response(
            session.get(report.url, timeout=DEFAULT_TIMEOUT),
            f"Senate report {report.url} after session refresh",
        )
    return response


def parse_senate_transaction_rows(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    transactions: list[str] = []
    for table_row in soup.find_all("tr"):
        cells = [normalize_text(cell.get_text(" ", strip=True)) for cell in table_row.find_all("td")]
        # Electronic PTR rows currently have at least eight columns. Requiring this avoids
        # scanning navigation/footer tables and reduces false positives.
        if len(cells) >= 8:
            transactions.append(" | ".join(cells))
    if not transactions:
        raise SourceChangedError(
            "Senate electronic PTR page contains no transaction rows with eight columns"
        )
    return transactions


def scan_house_report(session: Session, report: Report, config: Config) -> Alert | None:
    pdf_bytes = fetch_pdf_bytes(
        session,
        report.url,
        config,
        f"House PTR {report.metadata.get('document_id', report.report_id)}",
    )
    text = extract_pdf_text(pdf_bytes, config.max_ocr_pages)
    hits = find_keyword_hits(text, config.keywords)
    if not hits:
        return None
    return Alert(
        report_id=report.report_id,
        source=report.source,
        filer=report.filer,
        filed_date=report.filed_date,
        url=report.url,
        keywords=hits,
        snippet=text_snippet(text, hits),
        details=tuple(
            value
            for value in (
                f"District: {report.metadata.get('district')}" if report.metadata.get("district") else "",
                f"Document: {report.metadata.get('document_id')}" if report.metadata.get("document_id") else "",
            )
            if value
        ),
    )


def scan_senate_report(session: Session, report: Report, config: Config) -> Alert | None:
    response = _senate_page_response(session, report)
    data = response_bytes(
        response,
        f"Senate PTR {report.url}",
        config.max_download_bytes,
    )
    content_type = response.headers.get("Content-Type", "").lower()

    if report.format == "pdf" and not (
        data.startswith(b"%PDF") or "application/pdf" in content_type
    ):
        # Paper-filing routes sometimes return a viewer page instead of the PDF bytes.
        soup = BeautifulSoup(data, "html.parser")
        link = soup.find("a", href=re.compile(r"\.pdf(?:$|\?)", re.IGNORECASE))
        if not link or not link.get("href"):
            excerpt = normalize_text(soup.get_text(" ", strip=True))[:300]
            raise SourceChangedError(
                f"Senate paper PTR page contains no PDF link: {excerpt!r}"
            )
        pdf_url = urljoin(response.url, str(link["href"]))
        pdf_response = checked_response(
            session.get(pdf_url, timeout=DEFAULT_TIMEOUT),
            f"Senate paper PTR PDF {pdf_url}",
        )
        data = response_bytes(
            pdf_response,
            f"Senate paper PTR PDF {pdf_url}",
            config.max_download_bytes,
        )
        content_type = pdf_response.headers.get("Content-Type", "").lower()

    if data.startswith(b"%PDF") or "application/pdf" in content_type:
        text = extract_pdf_text(data, config.max_ocr_pages)
        hits = find_keyword_hits(text, config.keywords)
        if not hits:
            return None
        return Alert(
            report_id=report.report_id,
            source=report.source,
            filer=report.filer,
            filed_date=report.filed_date,
            url=report.url,
            keywords=hits,
            snippet=text_snippet(text, hits),
        )

    html = data.decode(response.encoding or "utf-8", errors="replace")
    rows = parse_senate_transaction_rows(html)
    matching_rows = [row for row in rows if find_keyword_hits(row, config.keywords)]
    if not matching_rows:
        return None
    combined = "\n".join(matching_rows)
    hits = find_keyword_hits(combined, config.keywords)
    return Alert(
        report_id=report.report_id,
        source=report.source,
        filer=report.filer,
        filed_date=report.filed_date,
        url=report.url,
        keywords=hits,
        snippet=text_snippet(combined, hits),
        details=tuple(matching_rows[:5]),
    )


def _truncate(value: str, limit: int) -> str:
    normalized = normalize_text(value)
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "…"


def send_pushover(session: Session, alert: Alert, config: Config) -> None:
    if config.no_notify:
        LOGGER.warning("Notification suppressed by --no-notify for %s", alert.report_id)
        return
    if not config.pushover_api_token or not config.pushover_user_key:
        raise NotificationError(
            "A disclosure matched, but PUSHOVER_API_TOKEN/PUSHOVER_USER_KEY are not configured"
        )

    title = _truncate(f"{alert.source.title()} disclosure match", 250)
    detail_lines = [
        f"Filer: {alert.filer}",
        f"Filed: {alert.filed_date}",
        f"Matched: {', '.join(alert.keywords)}",
    ]
    detail_lines.extend(alert.details[:3])
    if alert.snippet:
        detail_lines.append(alert.snippet)
    message = _truncate("\n".join(detail_lines), 1024)
    response = session.post(
        PUSHOVER_MESSAGES_URL,
        data={
            "token": config.pushover_api_token,
            "user": config.pushover_user_key,
            "title": title,
            "message": message,
            "url": alert.url,
            "url_title": "Open disclosure",
            "priority": "0",
        },
        timeout=DEFAULT_TIMEOUT,
    )
    try:
        checked_response(response, "Pushover notification")
    except MonitorError as exc:
        raise NotificationError(str(exc)) from exc
    try:
        body = response.json()
    except requests.JSONDecodeError as exc:
        raise NotificationError("Pushover returned non-JSON content") from exc
    if body.get("status") != 1:
        raise NotificationError(f"Pushover rejected notification: {body!r}")


def _selected_sources(value: str) -> tuple[str, ...]:
    if value == "all":
        return ("house", "senate")
    return (value,)


def _write_step_summary(result: RunResult) -> None:
    path_text = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path_text:
        return
    lines = [
        "## Congressional disclosure monitor",
        "",
        f"- Result: **{'success' if result.success else 'failure'}**",
        f"- Started: `{result.started_utc}`",
        f"- Finished: `{result.finished_utc}`",
    ]
    for source in sorted(result.source_counts):
        lines.append(
            f"- {source.title()}: {result.source_counts[source]} visible, "
            f"{result.new_counts.get(source, 0)} new, "
            f"{result.match_counts.get(source, 0)} matches, "
            f"{result.baseline_counts.get(source, 0)} baselined"
        )
    if result.errors:
        lines.extend(["", "### Errors", *[f"- {error}" for error in result.errors]])
    Path(path_text).open("a", encoding="utf-8").write("\n".join(lines) + "\n")


def run_monitor(config: Config, session: Session | None = None) -> RunResult:
    session = session or build_session(config.user_agent)
    started = iso_utc()
    result = RunResult(started_utc=started)

    try:
        if (
            config.require_pushover
            and not config.no_notify
            and (not config.pushover_api_token or not config.pushover_user_key)
        ):
            raise NotificationError(
                "REQUIRE_PUSHOVER is enabled, but PUSHOVER_API_TOKEN/PUSHOVER_USER_KEY are missing"
            )

        if not config.state_path.exists() and not config.allow_state_initialization:
            raise MonitorError(
                "Monitor state is missing and ALLOW_STATE_INITIALIZATION is false. "
                "Restore a prior state artifact or explicitly initialize a new baseline."
            )

        state, brand_new_state = load_state(config.state_path)
        state.last_attempt_utc = started
        save_state(config.state_path, state)
        current_year = utc_now().year
        for source in _selected_sources(config.source):
            if source == "house":
                reports = fetch_house_reports(
                    session,
                    years=(current_year - 1, current_year),
                    max_download_bytes=config.max_download_bytes,
                )
                scanner = scan_house_report
            else:
                reports = fetch_senate_reports(
                    session,
                    lookback_days=config.senate_lookback_days,
                )
                scanner = scan_senate_report

            result.source_counts[source] = len(reports)
            if not reports and not config.allow_empty_sources:
                raise SourceChangedError(
                    f"{source.title()} source returned zero PTRs; refusing to treat that as success"
                )

            source_has_state = bool(state.seen.get(source))
            source_bootstrap = brand_new_state or not source_has_state
            unseen = [report for report in reports if not state.is_seen(source, report.report_id)]
            result.new_counts[source] = len(unseen)
            result.match_counts[source] = 0
            result.baseline_counts[source] = 0

            if source_bootstrap and not config.bootstrap_alerts:
                timestamp = iso_utc()
                for report in reports:
                    state.mark_seen(source, report.report_id, timestamp)
                result.baseline_counts[source] = len(reports)
                save_state(config.state_path, state)
                LOGGER.info(
                    "Baselined %s existing %s reports without sending historical alerts",
                    len(reports),
                    source,
                )
                state.last_counts[source] = len(reports)
                continue

            for report in unseen:
                LOGGER.info(
                    "Scanning new %s report: %s (%s)", source, report.filer, report.url
                )
                alert = scanner(session, report, config)
                if alert:
                    # Mark a matching report seen only after notification succeeds.
                    send_pushover(session, alert, config)
                    result.alerts.append(asdict(alert))
                    result.match_counts[source] += 1
                    LOGGER.warning(
                        "Matched %s in %s report for %s",
                        ", ".join(alert.keywords),
                        source,
                        report.filer,
                    )
                state.mark_seen(source, report.report_id, iso_utc())
                # Persist incrementally so a later source/report failure does not duplicate
                # already-delivered alerts on the next run.
                save_state(config.state_path, state)

            state.last_counts[source] = len(reports)

        state.last_success_utc = iso_utc()
        save_state(config.state_path, state)
        result.success = True
        return result
    except Exception as exc:
        result.errors.append(f"{type(exc).__name__}: {exc}")
        raise
    finally:
        result.finished_utc = iso_utc()
        write_result(config.result_path, result)
        _write_step_summary(result)


def build_config(args: argparse.Namespace) -> Config:
    env = os.environ
    user_agent = env.get(
        "DISCLOSURE_USER_AGENT",
        "Mozilla/5.0 (compatible; MyETFDisclosureMonitor/2.0; +https://github.com/maglothinm/MyETF)",
    ).strip()
    if not user_agent:
        raise ValueError("DISCLOSURE_USER_AGENT must not be empty")
    return Config(
        keywords=parse_keywords(args.keywords or env.get("KEYWORDS")),
        state_path=Path(args.state_file or env.get("STATE_FILE", DEFAULT_STATE_PATH)),
        result_path=Path(args.result_file or env.get("RESULT_FILE", DEFAULT_RESULT_PATH)),
        source=args.source,
        bootstrap_alerts=(
            args.bootstrap_alerts
            or parse_bool(env.get("BOOTSTRAP_ALERTS"), default=False)
        ),
        no_notify=args.no_notify,
        senate_lookback_days=int(
            args.senate_lookback_days
            or env.get("SENATE_LOOKBACK_DAYS", DEFAULT_LOOKBACK_DAYS)
        ),
        max_download_bytes=int(
            env.get("MAX_DOWNLOAD_BYTES", DEFAULT_MAX_DOWNLOAD_BYTES)
        ),
        max_ocr_pages=int(env.get("OCR_MAX_PAGES", DEFAULT_MAX_OCR_PAGES)),
        user_agent=user_agent,
        pushover_api_token=env.get("PUSHOVER_API_TOKEN", "").strip(),
        pushover_user_key=env.get("PUSHOVER_USER_KEY", "").strip(),
        require_pushover=parse_bool(env.get("REQUIRE_PUSHOVER"), default=False),
        allow_empty_sources=parse_bool(env.get("ALLOW_EMPTY_SOURCES"), default=False),
        allow_state_initialization=parse_bool(
            env.get("ALLOW_STATE_INITIALIZATION"), default=True
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        choices=("all", "house", "senate"),
        default="all",
        help="Source to monitor (default: all)",
    )
    parser.add_argument(
        "--keywords",
        help="Comma-separated keywords; defaults to KEYWORDS or UNH/UnitedHealth variants",
    )
    parser.add_argument("--state-file", help="Override STATE_FILE")
    parser.add_argument("--result-file", help="Override RESULT_FILE")
    parser.add_argument(
        "--senate-lookback-days",
        type=int,
        help=f"Days to query from Senate (default: {DEFAULT_LOOKBACK_DAYS})",
    )
    parser.add_argument(
        "--bootstrap-alerts",
        action="store_true",
        help="Scan and alert on existing reports when state is first created",
    )
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="Log matches without sending Pushover notifications",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        config = build_config(args)
        result = run_monitor(config)
    except (MonitorError, ValueError, requests.RequestException) as exc:
        LOGGER.error("Monitoring failed: %s", exc)
        return 1
    except Exception:
        LOGGER.exception("Unexpected monitoring failure")
        return 1

    LOGGER.info(
        "Monitoring succeeded: visible=%s new=%s matches=%s baselined=%s",
        result.source_counts,
        result.new_counts,
        result.match_counts,
        result.baseline_counts,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
