"""Read the performance log (the same data as /perf.html), without the server running.

    python -m tools.perf_report                       # table of every stage, all time
    python -m tools.perf_report --since 24h           # last day (30m, 24h, 7d, 2w or an ISO date)
    python -m tools.perf_report --stage export        # only stages starting with "export"
    python -m tools.perf_report --group-by detail.kind,run
    python -m tools.perf_report --run current         # all | current | <run id>
    python -m tools.perf_report --runs                # the settings / hardware of each run
    python -m tools.perf_report --csv events.csv      # raw events for a spreadsheet
    python -m tools.perf_report --clear --yes         # delete the log (asks for --yes)

Columns: n = timings, err = failed ones, mean/p50/p95/max in seconds, x real = seconds of video processed
per second of work (higher is faster), conc = average jobs running at the same time.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clippie import perf  # noqa: E402
from clippie.db import init_db, session  # noqa: E402
from clippie.models import PerfEvent, PerfRun  # noqa: E402


def fmt(v, nd=2):
    return "-" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def table(rows: list[dict], group_by: list[str]) -> str:
    head = ["stage", *[g for g in group_by], "n", "err", "mean s", "p50 s", "p95 s", "max s", "total s", "x real", "conc"]
    body = [[r["stage"], *[fmt(r["group"].get(g), 0) for g in group_by], r["n"], r["errors"], fmt(r["mean_s"], 3),
             fmt(r["p50_s"], 3), fmt(r["p95_s"], 3), fmt(r["max_s"], 3), fmt(r["total_s"], 1), fmt(r["realtime_x"], 1),
             fmt(r["mean_concurrent"], 1)] for r in rows]
    widths = [max(len(str(x)) for x in col) for col in zip(head, *body)] if body else [len(h) for h in head]
    line = lambda cells: "  ".join(str(c).ljust(w) if i < 1 + len(group_by) else str(c).rjust(w)
                                   for i, (c, w) in enumerate(zip(cells, widths)))
    return "\n".join([line(head), line(["-" * w for w in widths]), *[line(b) for b in body]])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since"), ap.add_argument("--until"), ap.add_argument("--run"), ap.add_argument("--asset")
    ap.add_argument("--stage"), ap.add_argument("--status", choices=["ok", "error", "cancelled"])
    ap.add_argument("--group-by", default="", help="comma list of: run, asset, detail.<name>")
    ap.add_argument("--csv", metavar="FILE", help="write the raw events to a CSV file instead of a table")
    ap.add_argument("--runs", action="store_true", help="list runs with their settings and hardware")
    ap.add_argument("--clear", action="store_true", help="delete all performance data")
    ap.add_argument("--yes", action="store_true", help="confirm --clear")
    a = ap.parse_args()
    init_db()

    if a.clear:
        if not a.yes:
            print("This deletes the whole performance log. Run again with --yes to confirm.", file=sys.stderr)
            return 2
        with session() as s:
            n = s.query(PerfEvent).delete()
            s.query(PerfRun).delete()
            s.commit()
        print(f"deleted {n} events")
        return 0

    if a.runs:
        for r in perf.runs():
            print(f"{r['run_id']}  started {r['started_at']}  {r['events']} events")
            print("   host:  ", {k: v for k, v in r["host"].items()})
            print("   config:", r["config"])
        return 0

    try:
        filters = {"since": perf.parse_when(a.since), "until": perf.parse_when(a.until), "run": a.run,
                   "asset_id": a.asset, "stage": a.stage, "status": a.status}
        if a.csv:
            Path(a.csv).write_text(perf.events_csv(**filters), encoding="utf-8")
            print(f"wrote {a.csv}")
            return 0
        gb = [g.strip() for g in a.group_by.split(",") if g.strip()]
        rows = perf.summary(gb, **filters)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(table(rows, gb) if rows else "no measurements match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
