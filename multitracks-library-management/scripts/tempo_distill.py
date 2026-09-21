#!/usr/bin/env python3
"""Distill a dense (per-beat / humanized) MIDI tempo map into few MultiTracks rows.

Usage:
    python3 tempo_distill.py "<path/to/export.MID>" [tolerance_ms ...]

For each tolerance (default: 5 10 20 30 50), greedily fits the fewest
constant-tempo segments to the true beat grid and reports the rows plus the
verified worst-case click drift. Also reports the drift of a single static
tempo for comparison. Times print in the site's MM:SS:mmm form.

Use when a REAPER/Suno export has hundreds of set_tempo events: entering them
all on multitracks.com is impractical, but a static tempo accumulates drift
wherever the deviation is sustained (jitter around a mean is harmless).

MultiTracks RAMPS linearly between consecutive tempo rows (confirmed in
Playback 2026-08-22: a 114 -> 111 pair two minutes apart dragged the click
from the first bar). So each constant segment is emitted as a PAIR of rows --
its BPM at the segment start and again PIN_MS before the next segment starts --
which squeezes every ramp into a PIN_MS window, i.e. an effective step.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parse_midi import parse, fmt_mt


def bpm_at(rows, t):
    b = rows[0][1]
    for rt, rb in rows:
        if rt <= t:
            b = rb
        else:
            break
    return b


def beats(rows, end):
    ts, t = [], 0.0
    while t <= end:
        ts.append(t)
        t += 60.0 / bpm_at(rows, t)
    return ts


def max_drift(rows, true):
    a = beats(rows, true[-1] + 5)
    m, where = 0.0, 0.0
    for i, bt in enumerate(true):
        if i >= len(a):
            break
        d = abs(a[i] - bt)
        if d > m:
            m, where = d, bt
    return m, where


def segment(true, tol):
    n = len(true)
    rows, i = [], 0
    while i < n - 1:
        best = i + 1
        for j in range(i + 1, n):
            span = true[j] - true[i]
            bpm = 60.0 * (j - i) / span
            if all(abs(true[i] + (k - i) * 60.0 / bpm - true[k]) <= tol
                   for k in range(i, j + 1)):
                best = j
            else:
                break
        span = true[best] - true[i]
        rows.append((true[i], round(60.0 * (best - i) / span, 1)))
        i = best
    return rows


PIN_MS = 400  # ramp window left between a segment's closing row and the next row


def pinned(rows, end):
    """Expand step rows into site rows that defeat the ramp: BPM at start, BPM again just before the next row."""
    out = []
    for i, (t, b) in enumerate(rows):
        out.append((t, b))
        if i + 1 < len(rows):
            out.append((rows[i + 1][0] - PIN_MS / 1000, b))
    return out


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    path = Path(sys.argv[1])
    tols = [float(a) for a in sys.argv[2:]] or [5, 10, 20, 30, 50]

    _, _, tempo_list = parse(path)
    song_end = tempo_list[-1][0] + 5
    true = beats(tempo_list, song_end)

    static = [(0.0, round(sum(b for _, b in tempo_list) / len(tempo_list)))]
    m, w = max_drift(static, true)
    print(f"static {static[0][1]:g} BPM: max drift {m*1000:.0f} ms (at {fmt_mt(w)})\n")

    for tol in tols:
        rows = segment(true, tol / 1000)
        m, w = max_drift(rows, true)
        print(f"tolerance {tol:g} ms -> {len(rows)} rows "
              f"(verified max drift {m*1000:.0f} ms, worst at {fmt_mt(w)})")
        site = pinned(rows, song_end)
        print(f"    enter on the site ({len(site)} rows; pairs defeat the ramp):")
        for t, b in site:
            print(f"    {b:g} BPM @ {fmt_mt(t)}")
        print()


if __name__ == "__main__":
    main()
