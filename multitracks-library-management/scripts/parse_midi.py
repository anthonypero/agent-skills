#!/usr/bin/env python3
"""Parse a REAPER project-MIDI export and emit the data needed for MultiTracks.com.

Usage:
    python3 parse_midi.py "<path/to/export.MID>"

Reads the MIDI file's tempo map, time-signature meta events, and markers, then:
  - prints a summary (tempo changes, time signatures, markers) with times in both
    CSV format (MM:SS.mmm) and MultiTracks form format (MM:SS:mmm)
  - writes <song-folder-slug>-markers.csv and <song-folder-slug>-timesig.csv
    next to the MIDI file (two columns: zero-led MM:SS.mmm time, value)

The MIDI tick positions are exact; prefer these times over values read from
screenshots of REAPER's Region/Marker Manager, which round differently.
"""
import struct
import sys
from pathlib import Path


def read_vlq(d, i):
    v = 0
    while True:
        b = d[i]
        i += 1
        v = (v << 7) | (b & 0x7F)
        if not (b & 0x80):
            return v, i


def parse(path):
    data = path.read_bytes()
    assert data[:4] == b"MThd", "not a MIDI file"
    hdrlen = struct.unpack(">I", data[4:8])[0]
    _fmt, ntrks, division = struct.unpack(">HHH", data[8:14])

    events = []  # (tick, meta_type, payload)
    i = 8 + hdrlen
    for _ in range(ntrks):
        assert data[i:i + 4] == b"MTrk"
        tlen = struct.unpack(">I", data[i + 4:i + 8])[0]
        j = i + 8
        end = j + tlen
        tick = 0
        running = None
        while j < end:
            dt, j = read_vlq(data, j)
            tick += dt
            status = data[j]
            if status == 0xFF:
                meta = data[j + 1]
                length, k = read_vlq(data, j + 2)
                events.append((tick, meta, data[k:k + length]))
                j = k + length
            elif status in (0xF0, 0xF7):
                length, k = read_vlq(data, j + 1)
                j = k + length
            else:
                if status & 0x80:
                    running = status
                    j += 1
                j += 1 if (running >> 4) in (0xC, 0xD) else 2
        i = end

    tempos = sorted((tk, struct.unpack(">I", b"\x00" + p)[0])
                    for tk, m, p in events if m == 0x51)
    if not tempos or tempos[0][0] != 0:
        tempos.insert(0, (0, 500000))

    def tick_to_sec(tick):
        sec = 0.0
        for idx, (tt, us) in enumerate(tempos):
            nxt = tempos[idx + 1][0] if idx + 1 < len(tempos) else None
            if nxt is not None and tick > nxt:
                sec += (nxt - tt) / division * us / 1e6
            else:
                sec += (tick - tt) / division * us / 1e6
                break
        return sec

    timesigs = [(tick_to_sec(tk), f"{p[0]}/{2 ** p[1]}")
                for tk, m, p in sorted(events) if m == 0x58]
    markers = [(tick_to_sec(tk), p.decode("latin1"))
               for tk, m, p in sorted(events) if m == 0x06]
    tempo_list = [(tick_to_sec(tk), 60e6 / us) for tk, us in tempos]
    return timesigs, markers, tempo_list


def fmt_csv(s):
    m = int(s // 60)
    return f"{m:02d}:{s - m * 60:06.3f}"


def fmt_mt(s):
    return fmt_csv(s).replace(".", ":")


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    mid = Path(sys.argv[1])
    timesigs, markers, tempo_list = parse(mid)
    slug = mid.parent.name

    print("== Tempo changes (dedup consecutive) ==")
    last = None
    real_tempo_changes = []
    for t, bpm in tempo_list:
        if last is None or abs(bpm - last) > 0.0005:
            real_tempo_changes.append((t, bpm))
            print(f"  {fmt_csv(t)}  ({fmt_mt(t)})  {bpm:.3f} BPM")
        last = bpm
    print(f"\n== Time signatures ({len(timesigs)}) ==")
    for t, sig in timesigs:
        print(f"  {fmt_csv(t)}  ({fmt_mt(t)})  {sig}")
    print(f"\n== Markers ({len(markers)}) ==")
    for t, name in markers:
        print(f"  {fmt_csv(t)}  ({fmt_mt(t)})  {name}")

    mpath = mid.parent / f"{slug}-markers.csv"
    tpath = mid.parent / f"{slug}-timesig.csv"
    mpath.write_text("".join(f"{fmt_csv(t)},{n}\n" for t, n in markers))
    tpath.write_text("".join(f"{fmt_csv(t)},{s}\n" for t, s in timesigs))
    print(f"\nWrote {mpath}\nWrote {tpath}")


if __name__ == "__main__":
    main()
