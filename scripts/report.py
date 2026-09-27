#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
# SPDX-License-Identifier: Apache-2.0
"""Generate offline HTML, CSV and Markdown from a ledger or explicit legacy JSONL.

Legacy input is descriptive only: source order is not execution order, and absent
guards, measurement methods, units and decisions remain unknown.
"""
from __future__ import annotations

import argparse
import base64
import csv
import html
import io
import json
import math
from pathlib import Path

from experiment_ledger import load_ledger

UNKNOWN = "Not recorded"
HEADERS = ["attempt_id", "batch_id", "cell_id", "phase", "status", "settings", "change",
           "metric", "result", "unit", "n", "command", "method", "guard", "error",
           "decision", "next_change", "reason", "evidence"]


def _display(value):
    if value is None or value == "":
        return UNKNOWN
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _escape(value):
    return html.escape(_display(value), quote=True)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and (not isinstance(value, float) or math.isfinite(value))


def _legacy_measurements(raw, metrics):
    supplied = raw.get("measurements", {})
    if not isinstance(supplied, dict):
        raise ValueError("measurements must be an object keyed by metric")
    default_n = raw.get("n")
    if default_n is not None and (type(default_n) is not int or default_n < 0):
        raise ValueError("top-level n must be a nonnegative integer or null")
    measurements = {}
    # Retain metadata for missing results too, so the report can show Missing.
    for name in dict.fromkeys([*metrics, *supplied]):
        metadata = supplied.get(name, {})
        if not isinstance(metadata, dict):
            raise ValueError(f"Measurement {name} must be an object")
        measurement = dict(metadata)
        for field in ("command", "method", "unit"):
            if measurement.get(field) is not None and not isinstance(measurement[field], str):
                raise ValueError(f"Measurement {field} must be a string or null")
        # Explicit null remains unknown; only an absent per-metric n falls back.
        measurement.setdefault("n", default_n)
        n = measurement["n"]
        if n is not None and (type(n) is not int or n < 0):
            raise ValueError("Measurement n must be a nonnegative integer or null")
        measurements[name] = measurement
    return measurements


def _legacy_records(path):
    rows = []
    with Path(path).open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            try:
                raw = json.loads(line)
                if not isinstance(raw, dict):
                    raise ValueError("record must be an object")
                # Reject nonfinite values anywhere, including otherwise unrecognized fields.
                json.dumps(raw, allow_nan=False)
                run_id = raw.get("_run_id", raw.get("run_id"))
                if run_id is None:
                    run_id = f"source-line-{number}"
                if isinstance(run_id, bool) or not isinstance(run_id, (str, int)):
                    raise ValueError("run identity must be a string or integer")
                run_id = str(run_id)
                metrics = raw.get("values", raw.get("metrics"))
                if metrics is None:
                    metadata = {"n", "errors", "guard_ok", "run_id", "_run_id", "executed_order", "execution_order"}
                    metrics = {k: v for k, v in raw.items() if k not in metadata and (_number(v) or v is None)}
                if not isinstance(metrics, dict) or any(v is not None and not _number(v) for v in metrics.values()):
                    raise ValueError("metrics must contain finite numbers or null")
                settings = raw.get("settings", raw.get("factors"))
                if settings is None:
                    metadata = {"_run_id", "run_id", "split", "phase", "error", "status", "executed_at", "timestamp", "method", "command"}
                    settings = {k: v for k, v in raw.items() if isinstance(v, str) and k not in metadata}
                if not isinstance(settings, dict):
                    raise ValueError("settings must be an object")
                guard = raw.get("guard_ok")
                if guard is not None and type(guard) is not bool:
                    raise ValueError("guard_ok must be true, false or null")
                measurements = _legacy_measurements(raw, metrics)
                error = raw.get("error")
                if raw.get("errors"):
                    error = f"{raw['errors']} source errors" + (f": {error}" if error else "")
                rows.append({"attempt_id": f"source-line-{number}", "batch_id": None, "cell_id": run_id,
                             "phase": raw.get("phase", raw.get("split", "Unknown phase")),
                             "settings": settings, "metrics": metrics, "measurements": measurements,
                             "guard_ok": guard, "error": error,
                             "semantic_assessment": raw.get("semantic_assessment"),
                             "source_record": raw})
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Legacy line {number}: {exc}") from exc
    return rows


def load_report(*, ledger=None, legacy=None, title=None):
    if (ledger is None) == (legacy is None):
        raise ValueError("Choose exactly one ledger or legacy source")
    if ledger is not None:
        records = load_ledger(ledger)
        campaign = records[0]
        attempts = [r for r in records if r["record_type"] == "attempt"]
        decisions = [r for r in records if r["record_type"] == "decision"]
        source = str(ledger)
        label = "Validated campaign ledger"
    else:
        attempts = _legacy_records(legacy)
        decisions = []
        campaign = {"title": "Legacy experiment results", "recorded_at": None}
        source = str(legacy)
        label = "Legacy JSONL · descriptive evidence only"
    return {"title": title or campaign["title"], "source": source, "source_label": label,
            "campaign": campaign, "attempts": attempts, "decisions": decisions, "legacy": legacy is not None}


def _metrics(attempt):
    return {**{name: None for name in attempt.get("measurements", {})}, **attempt["metrics"]}


def _semantic_issue(attempt):
    receipt = attempt.get("semantic_assessment")
    if isinstance(receipt, dict) and receipt.get("required") is True:
        if receipt.get("status") != "ok" or receipt.get("measurement_available") is not True:
            return "Required semantic measurement unavailable: " + _display(receipt.get("status"))
    return None


def status(attempt):
    if attempt.get("error") or attempt.get("guard_ok") is False:
        return "failed"
    if _semantic_issue(attempt) or attempt.get("guard_ok") is None or not _metrics(attempt) or any(v is None for v in _metrics(attempt).values()):
        return "incomplete"
    return "complete"


def _decisions(model, attempt):
    return [d for d in model["decisions"] if attempt["attempt_id"] in d["attempt_ids"]]


def export_rows(model):
    rows = []
    for attempt in model["attempts"]:
        decisions = _decisions(model, attempt)
        guard = {True: "Passed", False: "Failed", None: UNKNOWN}[attempt.get("guard_ok")]
        # A failed attempt without metrics still has one export row.
        for metric, value in (_metrics(attempt) or {UNKNOWN: None}).items():
            measurement = attempt.get("measurements", {}).get(metric, {})
            rows.append([attempt["attempt_id"], _display(attempt.get("batch_id")), _display(attempt.get("cell_id")),
                         _display(attempt.get("phase")), status(attempt), _display(attempt["settings"]),
                         _display(attempt.get("change")), metric, "Missing" if value is None else value,
                         _display(measurement.get("unit")), _display(measurement.get("n")),
                         _display(measurement.get("command")), _display(measurement.get("method")), guard,
                         _display(attempt.get("error") or _semantic_issue(attempt)), "; ".join(d["decision_id"] for d in decisions) or UNKNOWN,
                         "; ".join(d["next_change"] for d in decisions) or UNKNOWN,
                         "; ".join(d["reason"] for d in decisions) or UNKNOWN,
                         json.dumps({"attempt": attempt, "decisions": decisions}, ensure_ascii=False, sort_keys=True)])
    return rows


def _csv_cell(value):
    # Spreadsheet applications may execute formulas even inside quoted CSV fields.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def render_csv(model):
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(HEADERS)
    writer.writerows([[_csv_cell(value) for value in row] for row in export_rows(model)])
    return buffer.getvalue()


def _md(value):
    import re
    escaped = html.escape(_display(value), quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|])", r"\\\1", escaped).replace("\r", " ").replace("\n", "<br>")


def render_markdown(model):
    lines = [f"# {_md(model['title'])}", "", _md(model["source_label"]), "",
             f"Source: {_md(model['source'])}", "", "Record order is append/source order; execution order is not inferred.", "",
             "| Attempt | Phase | Settings / change | Measurement | Result | Guard / error | Decision / next change / why |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for row in export_rows(model):
        cells = [row[0], row[3], f"{row[5]}; change: {row[6]}",
                 f"{row[7]}; command: {row[11]}; method: {row[12]}; unit: {row[9]}; n: {row[10]}",
                 row[8], f"{row[13]}; error: {row[14]}", f"{row[15]}; next: {row[16]}; why: {row[17]}"]
        lines.append("| " + " | ".join(_md(cell) for cell in cells) + " |")
    if not model["attempts"]:
        lines.extend(["", "No attempts recorded. Append an attempt and regenerate this report."])
    lines.extend(["", "## Decisions", ""])
    for decision in model["decisions"]:
        lines.append(f"- {_md(decision['decision_id'])}: {_md(decision['reason'])}. Next change: {_md(decision['next_change'])}.")
    if not model["decisions"]:
        lines.append("No decisions recorded.")
    lines.extend(["", "## Source evidence", "", "Campaign and every attempt/decision are retained below. Missing fields remain unknown.", ""])
    for record in [model["campaign"], *model["attempts"], *model["decisions"]]:
        lines.append("<pre>" + html.escape(json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2)) + "</pre>\n")
    return "\n".join(lines) + "\n"


def _data_url(content, mime):
    return f"data:{mime};base64," + base64.b64encode(content.encode("utf-8")).decode("ascii")


def _theme():
    local = Path(__file__).with_name("report_theme.css")
    if local.exists():
        return local.read_text(encoding="utf-8")
    # The wheel may place data-files under the installation prefix.
    import sys
    for root in (Path(sys.prefix), Path(__file__).parent):
        packaged = root / "share" / "agent-doe-engine" / "report_theme.css"
        if packaged.exists():
            return packaged.read_text(encoding="utf-8")
    raise ValueError("Report theme is missing; reinstall agent-doe-engine with report_theme.css")


def render_html(model):
    attempts = model["attempts"]
    counts = {key: sum(status(a) == key for a in attempts) for key in ("complete", "failed", "incomplete")}
    rows = []
    for attempt in attempts:
        state = status(attempt)
        settings = "".join(f"<dt>{_escape(k)}</dt><dd>{_escape(v)}</dd>" for k, v in attempt["settings"].items()) or f"<dd>{UNKNOWN}</dd>"
        metrics = []
        for name, value in _metrics(attempt).items():
            method = attempt.get("measurements", {}).get(name, {})
            metrics.append(f'<p><strong>{_escape(name)}: {"Missing" if value is None else _escape(value)}</strong><br><span class="metadata">Unit: {_escape(method.get("unit"))} · n: {_escape(method.get("n"))}</span></p>'
                           f'<details><summary>Measurement details</summary><p>Method: {_escape(method.get("method"))}</p><p>Command:</p><pre>{_escape(method.get("command"))}</pre></details>')
        decisions = _decisions(model, attempt)
        decision_text = "".join(f'<p><strong>{_escape(d["decision_id"])}</strong><br>Next: {_escape(d["next_change"])}<br>Why: {_escape(d["reason"])}</p>' for d in decisions) or UNKNOWN
        guard = {True: "Passed", False: "Failed", None: UNKNOWN}[attempt.get("guard_ok")]
        evidence = html.escape(json.dumps(attempt, ensure_ascii=False, sort_keys=True, indent=2))
        rows.append(f'<tr data-attempt="{_escape(attempt["attempt_id"])}" data-phase="{_escape(attempt.get("phase"))}" data-status="{state}">'
                    f'<td><strong>{_escape(attempt["attempt_id"])}</strong><p>{_escape(attempt.get("phase"))}</p><p class="metadata">Batch: {_escape(attempt.get("batch_id"))}<br>Cell: {_escape(attempt.get("cell_id"))}</p><details><summary>Source evidence</summary><pre>{evidence}</pre></details></td>'
                    f'<td><dl>{settings}</dl><p>Change: {_escape(attempt.get("change"))}</p></td>'
                    f'<td>{"".join(metrics) or "No metrics recorded"}</td>'
                    f'<td><strong class="{state}">{state.capitalize()}</strong><p>Guard: {guard}</p><p>Error: {_escape(attempt.get("error"))}</p><p>{_escape(_semantic_issue(attempt)) if _semantic_issue(attempt) else ""}</p></td>'
                    f'<td>{decision_text}</td></tr>')
    phases = sorted({_display(a.get("phase")) for a in attempts})
    options = "".join(f'<option value="{_escape(p)}">{_escape(p)}</option>' for p in phases)
    decisions_html = "".join(f'<article><h3>{_escape(d["decision_id"])}</h3><p>{_escape(d["reason"])}</p><p><strong>Next change:</strong> {_escape(d["next_change"])}</p><p class="metadata">Attempts: {_escape(d["attempt_ids"])} · Recorded: {_escape(d["recorded_at"])}</p><details><summary>Decision evidence</summary><pre>{html.escape(json.dumps(d, ensure_ascii=False, sort_keys=True, indent=2))}</pre></details></article>' for d in model["decisions"])
    payload = json.dumps({"headers": HEADERS, "rows": export_rows(model)}, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    csv_url = _data_url(render_csv(model), "text/csv")
    md_url = _data_url(render_markdown(model), "text/markdown")
    empty = '<p>No attempts recorded. Append an attempt and regenerate this report.</p>' if not attempts else ""
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_escape(model['title'])}</title><style>{_theme()}</style></head>
<body><main><header><p class="eyebrow">{'Legacy evidence' if model['legacy'] else 'Campaign ledger'} · saved report</p><h1>{_escape(model['title'])}</h1>
<p class="decision">{counts['failed']} failed and {counts['incomplete']} incomplete attempts need review.</p>
<details class="report-context"><summary>Report context</summary><p class="metadata">{_escape(model['source_label'])}<br>Source: {_escape(model['source'])}<br>Campaign recorded: {_escape(model['campaign'].get('recorded_at'))}</p>
<p class="metadata">Record order is append/source order. Execution order is not inferred. Complete means metrics and a passing guard were recorded; it does not establish promotion readiness.</p></details></header>
<section class="summary" aria-label="Attempt counts"><div><strong>{len(attempts)}</strong><span>Total attempts</span></div><div><strong>{counts['complete']}</strong><span>Complete attempts</span></div><div><strong>{counts['failed']}</strong><span>Failed attempts</span></div><div><strong>{counts['incomplete']}</strong><span>Incomplete attempts</span></div></section>
<section aria-labelledby="attempt-heading"><h2 id="attempt-heading">Review each attempt</h2>
<details id="report-tools"><summary>Filter and export</summary>
<div class="controls" id="filters" hidden><label>Search attempts<input id="search" type="search" placeholder="Search settings or evidence"></label><label>Phase<select id="phase"><option value="">All phases</option>{options}</select></label><label>Status<select id="status"><option value="">All statuses</option><option value="complete">Complete</option><option value="failed">Failed</option><option value="incomplete">Incomplete</option></select></label><button id="reset" type="button">Reset filters</button></div>
<div class="downloads"><a class="download" href="{csv_url}" download="report.csv">Export CSV</a><a class="download" href="{md_url}" download="report.md">Export Markdown</a><button id="export-filtered" type="button" hidden>Export filtered</button></div></details>
<p id="result-count" role="status" class="metadata">{len(attempts)} attempts · Scroll for all columns →</p>{empty}
<div class="table-scroll" role="region" aria-label="Experiment attempts; scroll horizontally for all columns" tabindex="0"><table><thead><tr><th scope="col">Attempt / phase</th><th scope="col">Settings / change</th><th scope="col">Measured results</th><th scope="col">Guard / error</th><th scope="col">Decision / next / why</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<p id="no-matches" hidden>No attempts match these filters. Reset filters to review all attempts.</p><div class="bottom-controls"><button id="show-more" type="button" hidden>Show more</button></div></section>
<section aria-labelledby="decision-heading"><h2 id="decision-heading">Decisions retain their supporting evidence</h2><p class="metadata">Decisions propose the next change. They do not replace the frozen campaign plan.</p><div class="decision-list">{decisions_html or '<article>No decisions recorded.</article>'}</div></section>
<details><summary>Campaign plan and provenance</summary><pre>{html.escape(json.dumps(model['campaign'], ensure_ascii=False, sort_keys=True, indent=2))}</pre></details>
<noscript><p>All attempts and exports are available. Enable JavaScript for filters and pagination.</p></noscript>
</main><script type="application/json" id="export-data">{payload}</script><script>
const rows = Array.from(document.querySelectorAll('tbody tr'));
const search = document.getElementById('search'), phase = document.getElementById('phase'), statusFilter = document.getElementById('status');
const more = document.getElementById('show-more');
let limit = matchMedia('(max-width: 699px)').matches ? 5 : 20;
let matching = rows;
function filterRows(reset) {{
  if (reset) limit = matchMedia('(max-width: 699px)').matches ? 5 : 20;
  const query = search.value.trim().toLowerCase();
  matching = rows.filter(row => (!phase.value || row.dataset.phase === phase.value) && (!statusFilter.value || row.dataset.status === statusFilter.value) && (!query || row.textContent.toLowerCase().includes(query)));
  const visible = new Set(matching.slice(0, limit));
  rows.forEach(row => row.hidden = !visible.has(row));
  more.hidden = matching.length <= limit;
  document.getElementById('no-matches').hidden = matching.length !== 0 || rows.length === 0;
  document.getElementById('result-count').textContent = `${{Math.min(limit, matching.length)}} of ${{matching.length}} matches (${{rows.length}} total) · Scroll for all columns →`;
}}
let timer;
search.addEventListener('input', () => {{ clearTimeout(timer); timer = setTimeout(() => filterRows(true), 300); }});
[phase, statusFilter].forEach(input => input.addEventListener('change', () => filterRows(true)));
document.getElementById('reset').addEventListener('click', () => {{ search.value = ''; phase.value = ''; statusFilter.value = ''; filterRows(true); }});
more.addEventListener('click', () => {{ limit += matchMedia('(max-width: 699px)').matches ? 5 : 20; filterRows(false); }});
document.getElementById('export-filtered').addEventListener('click', () => {{
  filterRows(false);
  const data = JSON.parse(document.getElementById('export-data').textContent);
  const ids = new Set(matching.map(row => row.dataset.attempt));
  const selected = [data.headers, ...data.rows.filter(row => ids.has(String(row[0])))];
  const csv = selected.map(row => row.map(value => {{ let s = String(value); if (typeof value === 'string' && /^[=+@-]/.test(s.trimStart())) s = "'" + s; return '"' + s.replace(/"/g, '""') + '"'; }}).join(',')).join(String.fromCharCode(13,10));
  const url = URL.createObjectURL(new Blob([csv], {{type:'text/csv;charset=utf-8'}}));
  const link = document.createElement('a'); link.href = url; link.download = 'report-filtered.csv'; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}});
document.getElementById('filters').hidden = false; document.getElementById('export-filtered').hidden = false; document.getElementById('report-tools').open = matchMedia('(min-width: 700px)').matches; filterRows(false);
</script></body></html>'''


def write_report(model, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    paths = {}
    for extension, renderer in (("html", render_html), ("csv", render_csv), ("md", render_markdown)):
        path = output / f"report.{extension}"
        path.write_text(renderer(model), encoding="utf-8")
        paths[extension] = str(path)
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--ledger", type=Path)
    source.add_argument("--legacy", type=Path, help="Explicitly label older result records as legacy evidence")
    parser.add_argument("--output", type=Path, required=True, help="Directory for report.html, report.csv and report.md")
    parser.add_argument("--title")
    args = parser.parse_args(argv)
    try:
        model = load_report(ledger=args.ledger, legacy=args.legacy, title=args.title)
        print(json.dumps(write_report(model, args.output)))
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, f"Report error: {exc}\n")


if __name__ == "__main__":
    main()
