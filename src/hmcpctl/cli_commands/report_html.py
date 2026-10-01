"""Self-contained HTML rendering of the fleet utilization report (#1254).

Every value taken from a report row or a profile name is HMC- or operator-supplied text,
so it reaches the page only through ``html.escape``; the page loads nothing from the network.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime
from html import escape

_UNKNOWN = "unknown"
_FIGURES = (
    ("cpu_configurable", "CPU configurable"),
    ("cpu_allocated", "CPU allocated"),
    ("cpu_util_pct", "CPU %"),
    ("mem_configurable_mib", "Memory configurable (GiB)"),
    ("mem_allocated_mib", "Memory allocated (GiB)"),
    ("mem_util_pct", "Memory %"),
    ("cpu_idle_reserved", "Idle CPU"),
    ("mem_idle_reserved_mib", "Idle memory (GiB)"),
    ("partitions_running", "Running"),
    ("partitions_not_activated", "Not activated"),
    ("disk_util_pct", "Disk %"),
    ("slots_util_pct", "Slots %"),
    ("sriov_util_pct", "SR-IOV %"),
)
_SYSTEM_COLUMNS = (
    ("system", "System"),
    ("profiles", "HMC profiles"),
    ("machine_type", "Type"),
    ("model", "Model"),
    ("serial", "Serial"),
    ("state", "State"),
    *_FIGURES,
    ("notes", "Notes"),
)
_HMC_COLUMNS = (
    ("profiles", "HMC profile"),
    ("systems", "Systems"),
    *_FIGURES,
    ("notes", "Unknown figures"),
)
# Tile title, its utilization column (or None), and the (column, label) figures it shows.
_TILES = (
    ("CPU", "cpu_util_pct", (("cpu_allocated", "allocated"), ("cpu_free", "free"))),
    (
        "Memory (GiB)",
        "mem_util_pct",
        (("mem_allocated_mib", "allocated"), ("mem_free_mib", "free")),
    ),
    (
        "Disk (GiB)",
        "disk_util_pct",
        (
            ("disk_internal_assigned_mib", "internal assigned"),
            ("disk_internal_free_mib", "internal free"),
            ("disk_san_assigned_mib", "SAN assigned"),
            ("disk_san_free_mib", "SAN free"),
        ),
    ),
    (
        "I/O slots",
        "slots_util_pct",
        (
            ("slots_assigned", "assigned"),
            ("slots_sriov", "SR-IOV"),
            ("slots_unassigned", "unassigned"),
            ("slots_empty", "empty"),
        ),
    ),
    (
        "SR-IOV logical ports",
        "sriov_util_pct",
        (("sriov_logical_ports", "supported"), ("sriov_logical_ports_free", "free")),
    ),
    (
        "Idle partitions",
        None,
        (
            ("partitions_not_activated", "not activated"),
            ("cpu_idle_reserved", "CPU reserved"),
            ("mem_idle_reserved_mib", "memory reserved (GiB)"),
        ),
    ),
)
_SCRIPT = """
document.querySelectorAll("table.sortable th").forEach((th) => {
  th.addEventListener("click", () => {
    const body = th.closest("table").tBodies[0], col = th.cellIndex;
    const rows = Array.from(body.rows), key = (r) => r.cells[col].dataset.sort;
    const numeric = rows.every((r) => key(r) === "" || !isNaN(Number(key(r))));
    const up = th.dataset.dir !== "asc";
    th.dataset.dir = up ? "asc" : "desc";
    rows.sort((a, b) => {
      const x = key(a), y = key(b);
      if (x === "" || y === "") return (x === "") - (y === "");
      const order = numeric ? Number(x) - Number(y) : x.localeCompare(y);
      return up ? order : -order;
    });
    rows.forEach((r) => body.appendChild(r));
  });
});
"""
_SCRIPT_HASH = base64.b64encode(hashlib.sha256(_SCRIPT.encode()).digest()).decode()
_STYLE = """
body { font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; margin: 2em;
  color: #1d1d1f; }
.warning { border-left: 4px solid #b3261e; padding: 0.4em 0.8em; background: #fbeaea; }
.tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(15em, 1fr));
  gap: 1em; }
.tile { border: 1px solid #ccc; border-radius: 6px; padding: 0.6em 1em; }
.tile h3 { margin: 0 0 0.3em; font-size: 1em; }
.tile .pct { font-size: 1.6em; margin: 0; }
.tile dl { display: grid; grid-template-columns: auto auto; margin: 0.4em 0 0; }
.tile dd { margin: 0; text-align: right; }
table { border-collapse: collapse; margin: 0.5em 0 1.5em; font-size: 0.85em; }
th, td { border: 1px solid #ccc; padding: 0.25em 0.5em; text-align: left; }
th { background: #f2f2f2; }
table.sortable th { cursor: pointer; }
.unknown { font-style: italic; color: #8a4b00; }
svg.bar { width: 5em; height: 0.6em; display: block; }
svg.bar .track { fill: #e3e3e3; }
svg.bar .fill { fill: #2f6fb3; }
@media print {
  body { margin: 0; font-size: 9pt; }
  table.sortable th { cursor: auto; }
  .tile, tr { break-inside: avoid; }
}
"""


def _number(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def _unknown() -> str:
    return f'<span class="unknown">{_UNKNOWN}</span>'


def _shown(column: str, value: str) -> str:
    """Escaped display text for one value: unknown marked, MiB columns as GiB."""
    if value == _UNKNOWN:
        return _unknown()
    number = _number(value)
    if column.endswith("_mib") and number is not None:
        return f"{number / 1024:,.1f}"
    return escape(value, quote=True)


def _bar(pct: float) -> str:
    width = min(max(pct, 0.0), 100.0)
    return (
        f'<svg class="bar" viewBox="0 0 100 6" role="img" aria-label="{pct:g}%">'
        '<rect class="track" width="100" height="6"/>'
        f'<rect class="fill" width="{width:g}" height="6"/></svg>'
    )


def _cell(column: str, value: str) -> str:
    if value == _UNKNOWN:
        return f'<td class="unknown" data-sort="">{_UNKNOWN}</td>'
    number = _number(value)
    sort = value if number is not None else value.lower()
    bar = _bar(number) if column.endswith("_util_pct") and number is not None else ""
    return (
        f'<td data-sort="{escape(sort, quote=True)}">{_shown(column, value)}{bar}</td>'
    )


def _table(
    rows: list[dict[str, str]], columns: tuple[tuple[str, str], ...], *, sortable: bool
) -> str:
    head = "".join(f'<th scope="col">{escape(label)}</th>' for _, label in columns)
    body = "".join(
        "<tr>"
        + "".join(_cell(column, row.get(column, "")) for column, _ in columns)
        + "</tr>"
        for row in rows
    )
    opening = '<table class="sortable">' if sortable else "<table>"
    return f"{opening}<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _tiles(fleet: dict[str, str]) -> str:
    if fleet.get("systems") == "0":
        return "<p>No systems were surveyed.</p>"
    tiles = []
    for title, pct_column, figures in _TILES:
        pct = ""
        if pct_column is not None:
            number = _number(fleet[pct_column])
            pct = (
                f'<p class="pct">{_unknown()}</p>'
                if number is None
                else f'<p class="pct">{number:g}%</p>{_bar(number)}'
            )
        listed = "".join(
            f"<dt>{escape(label)}</dt><dd>{_shown(column, fleet[column])}</dd>"
            for column, label in figures
        )
        tiles.append(
            f'<section class="tile"><h3>{escape(title)}</h3>{pct}<dl>{listed}</dl></section>'
        )
    return f'<div class="tiles">{"".join(tiles)}</div>'


def _failures(failures: list[dict[str, str]]) -> str:
    if not failures:
        return "<p>none</p>"
    body = "".join(
        f"<tr><td>{escape(row['profiles'], quote=True)}</td>"
        f"<td>{escape(row['notes'], quote=True)}</td></tr>"
        for row in failures
    )
    return (
        '<table><thead><tr><th scope="col">Profile</th><th scope="col">Reason</th>'
        f"</tr></thead><tbody>{body}</tbody></table>"
    )


def render_html(
    rows: list[dict[str, str]], profiles: tuple[str, ...], generated: datetime
) -> str:
    """Render ``report_rows`` output as one self-contained HTML page."""
    by_type: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_type.setdefault(row["row_type"], []).append(row)
    fleet = by_type["fleet"][0]
    stamp = generated.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    names = ", ".join(escape(name, quote=True) for name in profiles)
    notes = f"<p>{escape(fleet['notes'], quote=True)}</p>" if fleet["notes"] else ""
    policy = (
        "default-src 'none'; style-src 'unsafe-inline'; "
        f"script-src 'sha256-{_SCRIPT_HASH}'"
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{policy}">
<title>Fleet utilization {stamp}</title>
<style>{_STYLE}</style>
</head>
<body>
<h1>Fleet utilization</h1>
<p>Generated {stamp}</p>
<p>Profiles surveyed: {names}</p>
<p class="warning">This report holds internal hostnames, system names and serial numbers.
Never commit it or post it in a public place.</p>
<h2>Fleet</h2>
{_tiles(fleet)}
{notes}
<h2>Per HMC</h2>
{_table(by_type.get("hmc", []), _HMC_COLUMNS, sortable=False)}
<h2>Systems</h2>
{_table(by_type.get("system", []), _SYSTEM_COLUMNS, sortable=True)}
<h2>Failed profiles</h2>
{_failures(by_type.get("failure", []))}
<script>{_SCRIPT}</script>
</body>
</html>
"""
