from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from scripts.monitor_disclosures import (
    Alert,
    Config,
    MonitorError,
    MonitorState,
    SourceChangedError,
    extract_pdf_text,
    extract_report_link,
    find_keyword_hits,
    load_state,
    parse_house_index,
    parse_senate_result_rows,
    parse_senate_transaction_rows,
    save_state,
    text_snippet,
)


def house_zip(text: str, year: int = 2026) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{year}FD.txt", text)
    return buffer.getvalue()


def simple_text_pdf(text: str) -> bytes:
    """Build a tiny dependency-free PDF with one text-layer line."""
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 18 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /Resources "
            b"<< /Font << /F1 4 0 R >> >> /MediaBox [0 0 612 792] "
            b"/Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode("ascii"))
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(output)


def test_parse_house_index_uses_ptr_rows_and_builds_pdf_url() -> None:
    text = (
        "Prefix\tLast\tFirst\tSuffix\tFilingType\tStateDst\tYear\tFilingDate\tDocID\n"
        "Hon.\tExample\tAlex\tJr.\tP\tNY01\t2026\t7/20/2026\t20039999\n"
        "\tAnnual\tCasey\t\tA\tCA12\t2026\t7/19/2026\t20039998\n"
    )
    reports = parse_house_index(house_zip(text), 2026)
    assert len(reports) == 1
    report = reports[0]
    assert report.report_id == "house:2026:20039999"
    assert report.filer == "Hon. Alex Example Jr."
    assert report.metadata["district"] == "NY01"
    assert report.url.endswith("/ptr-pdfs/2026/20039999.pdf")


def test_parse_house_index_fails_closed_on_schema_change() -> None:
    text = "Name\tType\nAlex Example\tP\n"
    with pytest.raises(SourceChangedError, match="missing expected columns"):
        parse_house_index(house_zip(text), 2026)


def test_extract_pdf_text_reads_a_real_text_layer() -> None:
    text = extract_pdf_text(simple_text_pdf("Purchased UNH common stock"), max_ocr_pages=3)
    assert "UNH" in text


def test_keyword_matching_is_case_insensitive_and_ticker_bounded() -> None:
    keywords = ("UNH", "UnitedHealth")
    assert find_keyword_hits("Purchased unh common stock", keywords) == ("UNH",)
    assert find_keyword_hits("UNITEDHEALTH GROUP INC", keywords) == ("UnitedHealth",)
    assert find_keyword_hits("This word is UNHINGED", keywords) == ()
    assert "UnitedHealth" in text_snippet("x " * 200 + "UnitedHealth Group", keywords)


def test_extract_senate_link_and_rows() -> None:
    rows = [
        [
            "Alex",
            "Example",
            "Periodic Transaction Report",
            '<a href="/search/view/ptr/abc123/">View</a>',
            "07/20/2026",
        ]
    ]
    reports = parse_senate_result_rows(rows)
    assert reports[0].filer == "Alex Example"
    assert reports[0].url == "https://efdsearch.senate.gov/search/view/ptr/abc123/"
    assert reports[0].format == "html"
    assert extract_report_link('<a href="/search/view/paper/xyz/">PDF</a>').endswith(
        "/search/view/paper/xyz/"
    )


def test_senate_result_row_without_link_fails_closed() -> None:
    with pytest.raises(SourceChangedError, match="no report link"):
        parse_senate_result_rows([["A", "B", "PTR", "not a link", "today"]])


def test_parse_senate_transaction_rows_ignores_short_tables() -> None:
    html = """
    <table><tbody><tr><td>Navigation</td><td>UNH</td></tr></tbody></table>
    <table><tbody>
      <tr>
        <td>1</td><td>07/01/2026</td><td>Owner</td><td>UNH</td>
        <td>UnitedHealth Group Inc.</td><td>Stock</td><td>Purchase</td><td>$1,001-$15,000</td>
      </tr>
    </tbody></table>
    """
    rows = parse_senate_transaction_rows(html)
    assert rows == [
        "1 | 07/01/2026 | Owner | UNH | UnitedHealth Group Inc. | Stock | Purchase | $1,001-$15,000"
    ]


def test_state_round_trip_and_atomic_shape(tmp_path: Path) -> None:
    path = tmp_path / "state" / "disclosures.json"
    state = MonitorState()
    state.mark_seen("house", "house:2026:1", "2026-07-22T12:00:00Z")
    state.last_success_utc = "2026-07-22T12:00:00Z"
    save_state(path, state)

    loaded, is_new = load_state(path)
    assert is_new is False
    assert loaded.is_seen("house", "house:2026:1")
    assert loaded.last_success_utc == "2026-07-22T12:00:00Z"
    payload = json.loads(path.read_text())
    assert payload["version"] == 2


def test_missing_state_is_bootstrap(tmp_path: Path) -> None:
    state, is_new = load_state(tmp_path / "missing.json")
    assert is_new is True
    assert state.seen == {"house": {}, "senate": {}}


def make_config(tmp_path: Path, *, bootstrap_alerts: bool = False) -> Config:
    return Config(
        keywords=("UNH", "UnitedHealth"),
        state_path=tmp_path / "state.json",
        result_path=tmp_path / "result.json",
        source="house",
        bootstrap_alerts=bootstrap_alerts,
        no_notify=True,
        senate_lookback_days=120,
        max_download_bytes=10_000_000,
        max_ocr_pages=10,
        user_agent="test-agent",
        pushover_api_token="",
        pushover_user_key="",
        require_pushover=False,
        allow_empty_sources=False,
        allow_state_initialization=True,
    )


def test_monitor_refuses_unexpected_state_loss(tmp_path: Path) -> None:
    import scripts.monitor_disclosures as monitor

    config = make_config(tmp_path)
    config = Config(
        keywords=config.keywords,
        state_path=config.state_path,
        result_path=config.result_path,
        source=config.source,
        bootstrap_alerts=config.bootstrap_alerts,
        no_notify=config.no_notify,
        senate_lookback_days=config.senate_lookback_days,
        max_download_bytes=config.max_download_bytes,
        max_ocr_pages=config.max_ocr_pages,
        user_agent=config.user_agent,
        pushover_api_token=config.pushover_api_token,
        pushover_user_key=config.pushover_user_key,
        require_pushover=config.require_pushover,
        allow_empty_sources=config.allow_empty_sources,
        allow_state_initialization=False,
    )

    with pytest.raises(MonitorError, match="Monitor state is missing"):
        monitor.run_monitor(config, session=object())

    result = json.loads((tmp_path / "result.json").read_text())
    assert result["success"] is False
    assert "ALLOW_STATE_INITIALIZATION" in result["errors"][0]


def sample_report(report_id: str) -> object:
    from scripts.monitor_disclosures import Report

    return Report(
        report_id=report_id,
        source="house",
        filer="Alex Example",
        filed_date="07/20/2026",
        url="https://example.invalid/report.pdf",
        format="pdf",
    )


def test_run_monitor_baselines_first_run_without_scanning(tmp_path: Path, monkeypatch) -> None:
    import scripts.monitor_disclosures as monitor

    report = sample_report("house:2026:1")
    monkeypatch.setattr(monitor, "fetch_house_reports", lambda *args, **kwargs: [report])

    def should_not_scan(*args, **kwargs):
        raise AssertionError("bootstrap should not scan historical reports")

    monkeypatch.setattr(monitor, "scan_house_report", should_not_scan)
    result = monitor.run_monitor(make_config(tmp_path), session=object())
    assert result.success is True
    assert result.baseline_counts == {"house": 1}
    state, _ = load_state(tmp_path / "state.json")
    assert state.is_seen("house", "house:2026:1")


def test_run_monitor_scans_only_new_reports(tmp_path: Path, monkeypatch) -> None:
    import scripts.monitor_disclosures as monitor

    old = sample_report("house:2026:old")
    new = sample_report("house:2026:new")
    state = MonitorState()
    state.mark_seen("house", old.report_id, "2026-07-21T00:00:00Z")
    save_state(tmp_path / "state.json", state)

    monkeypatch.setattr(
        monitor, "fetch_house_reports", lambda *args, **kwargs: [old, new]
    )
    scanned = []

    def scan(_session, report, _config):
        scanned.append(report.report_id)
        return None

    monkeypatch.setattr(monitor, "scan_house_report", scan)
    result = monitor.run_monitor(make_config(tmp_path), session=object())
    assert result.new_counts == {"house": 1}
    assert scanned == [new.report_id]
    loaded, _ = load_state(tmp_path / "state.json")
    assert loaded.is_seen("house", new.report_id)


def test_failed_notification_does_not_mark_match_seen(tmp_path: Path, monkeypatch) -> None:
    import scripts.monitor_disclosures as monitor
    from scripts.monitor_disclosures import NotificationError

    old = sample_report("house:2026:old")
    new = sample_report("house:2026:new")
    state = MonitorState()
    state.mark_seen("house", old.report_id, "2026-07-21T00:00:00Z")
    save_state(tmp_path / "state.json", state)
    monkeypatch.setattr(
        monitor, "fetch_house_reports", lambda *args, **kwargs: [old, new]
    )
    monkeypatch.setattr(
        monitor,
        "scan_house_report",
        lambda *_: Alert(
            report_id=new.report_id,
            source="house",
            filer="Alex Example",
            filed_date="07/20/2026",
            url="https://example.invalid/report.pdf",
            keywords=("UNH",),
            snippet="UNH purchase",
        ),
    )
    monkeypatch.setattr(
        monitor,
        "send_pushover",
        lambda *_: (_ for _ in ()).throw(NotificationError("delivery failed")),
    )

    with pytest.raises(NotificationError, match="delivery failed"):
        monitor.run_monitor(make_config(tmp_path), session=object())
    loaded, _ = load_state(tmp_path / "state.json")
    assert not loaded.is_seen("house", new.report_id)
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["success"] is False
    assert "NotificationError" in result["errors"][0]
