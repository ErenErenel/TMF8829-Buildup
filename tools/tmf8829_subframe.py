#!/usr/bin/env python3
"""Stage 3b: work out how the TMF8829 splits 32x32 / 48x32 across two sub-frames.

At those resolutions the device sends half the zones per frame, flagged by
sub_result (bit 6 of the result layout). Assembling them needs the interleaving
pattern, which is not documented and not yet known.

This does the hypothesis testing in Python rather than firmware, so trying a new
candidate is an edit-and-rerun instead of an edit-and-reflash -- the reason
streamZoneFrameBinary() emits zones in raw device order.

    # capture a pair-rich sample against a structured scene (hand at an edge)
    python tools/tmf8829_subframe.py capture --port COM5 --seconds 20

    # score every candidate assembly against that capture
    python tools/tmf8829_subframe.py solve

Method: a correctly assembled depth image of a real scene is *spatially smooth*
-- neighbouring zones see nearly the same thing. A wrong interleave shuffles
unrelated zones next to each other, which shows up as high total variation. So
score each candidate by mean absolute difference between 4-neighbours and take
the lowest. Crucially the wrong answers fail in a characteristic way: a
row-interleave error striped the wrong way produces horizontal banding, so the
per-axis breakdown is reported too, not just the total.
"""

from __future__ import annotations

import argparse
import pickle
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from tmf8829_viewer import SerialReader  # noqa: E402


DEFAULT_CAPTURE = Path(__file__).parent.parent / ".captures" / "subframes.pkl"


# ---------------------------------------------------------------------------
# Candidate assemblies
#
# Each takes the two sub-frames (A = sub_result clear, B = set), each a flat
# array of N zones, plus the full grid shape, and returns a (h, w) image.
# Each is a guess about how the device splits the array; none is confirmed.
# ---------------------------------------------------------------------------
def _rows_alternating(a, b, h, w):
    """A holds even rows, B holds odd rows."""
    out = np.zeros((h, w), a.dtype)
    out[0::2, :] = a.reshape(h // 2, w)
    out[1::2, :] = b.reshape(h // 2, w)
    return out


def _rows_blocked(a, b, h, w):
    """A holds the top half, B the bottom half."""
    return np.vstack([a.reshape(h // 2, w), b.reshape(h // 2, w)])


def _cols_alternating(a, b, h, w):
    """A holds even columns, B holds odd columns."""
    out = np.zeros((h, w), a.dtype)
    out[:, 0::2] = a.reshape(h, w // 2)
    out[:, 1::2] = b.reshape(h, w // 2)
    return out


def _cols_blocked(a, b, h, w):
    """A holds the left half, B the right half."""
    return np.hstack([a.reshape(h, w // 2), b.reshape(h, w // 2)])


def _checkerboard(a, b, h, w):
    """A holds one colour of a checkerboard, B the other."""
    out = np.zeros((h, w), a.dtype)
    mask = (np.indices((h, w)).sum(0) % 2) == 0
    out[mask] = a
    out[~mask] = b
    return out


def _checkerboard_inv(a, b, h, w):
    return _checkerboard(b, a, h, w)


CANDIDATES = {
    "rows_alternating": _rows_alternating,
    "rows_alternating_swapped": lambda a, b, h, w: _rows_alternating(b, a, h, w),
    "rows_blocked": _rows_blocked,
    "rows_blocked_swapped": lambda a, b, h, w: _rows_blocked(b, a, h, w),
    "cols_alternating": _cols_alternating,
    "cols_alternating_swapped": lambda a, b, h, w: _cols_alternating(b, a, h, w),
    "cols_blocked": _cols_blocked,
    "cols_blocked_swapped": lambda a, b, h, w: _cols_blocked(b, a, h, w),
    "checkerboard": _checkerboard,
    "checkerboard_swapped": _checkerboard_inv,
}


def roughness(img: np.ndarray) -> tuple[float, float, float]:
    """Mean |difference| between 4-neighbours, split by axis.

    Reported per-axis because the failure modes are directional: a bad
    row-interleave leaves rows smooth but columns jagged, and vice versa. A
    single combined number would hide which axis is wrong.
    """
    f = img.astype(np.float64)
    vert = np.abs(np.diff(f, axis=0)).mean() if f.shape[0] > 1 else 0.0
    horz = np.abs(np.diff(f, axis=1)).mean() if f.shape[1] > 1 else 0.0
    return vert + horz, vert, horz


def capture(args) -> None:
    reader = SerialReader(args.port, args.baud)
    reader.start()
    print(f"capturing {args.seconds}s from {args.port} -- aim at a structured scene")
    frames, t0 = [], time.monotonic()
    try:
        while time.monotonic() - t0 < args.seconds:
            f = reader.take()
            if f is None:
                time.sleep(0.003)
                continue
            frames.append(f)
    finally:
        reader.close()

    if not frames:
        print("No frames received. Is the board measuring with binary streaming on "
              "('m' then 'b')?")
        return

    subs = [f for f in frames if f.is_subframe or f.zone_count != f.grid_w * f.grid_h]
    print(f"{len(frames)} frames, {len(subs)} carrying partial zone counts")
    if not subs:
        # Still worth saving -- capture doubles as the general recorder, and
        # 8x8/16x16 are single-frame modes with nothing to solve.
        print("No sub-frames seen, so there is no interleave to solve here. "
              "Saving anyway; for 'solve' use 32x32 or 48x32 (send 's', then 'c' "
              "until 'Preconfig 69' or '71', then 'm').")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as fh:
        pickle.dump([(f.frame_num, f.is_subframe, f.zone_count, f.grid_w, f.grid_h,
                      f.distance_mm, f.confidence) for f in frames], fh)
    print(f"saved {len(frames)} frames -> {out}")


def solve(args) -> None:
    with Path(args.capture).open("rb") as fh:
        raw = pickle.load(fh)
    frames = [dict(zip(("num", "sub", "n", "w", "h", "dist", "conf"), r)) for r in raw]

    # Pair each frame with the next one carrying the opposite sub_result flag.
    # Consecutive frame numbers only -- a dropped frame between halves would
    # pair data from different measurements and quietly corrupt the scoring.
    pairs = []
    for first, second in zip(frames, frames[1:]):
        if first["sub"] == second["sub"]:
            continue
        if second["num"] != first["num"] + 1:
            continue
        if first["n"] != second["n"] or first["n"] * 2 != first["w"] * first["h"]:
            continue
        a, b = (first, second) if not first["sub"] else (second, first)
        pairs.append((a, b))

    if not pairs:
        print("No consecutive sub-frame pairs found. Need 32x32 or 48x32 data "
              "where zone_count is exactly half of grid_w*grid_h.")
        return

    w, h = pairs[0][0]["w"], pairs[0][0]["h"]
    print(f"{len(pairs)} pairs at {w}x{h} ({pairs[0][0]['n']} zones per sub-frame)\n")

    scores = {}
    for name, fn in CANDIDATES.items():
        totals = []
        for a, b in pairs[: args.limit]:
            try:
                img = fn(a["dist"], b["dist"], h, w)
            except ValueError:
                totals = None       # shape doesn't divide evenly for this mode
                break
            totals.append(roughness(img))
        if not totals:
            continue
        arr = np.array(totals)
        scores[name] = arr.mean(axis=0)

    # Baseline: how rough is a single sub-frame on its own? Any assembly that
    # scores worse than this is actively destroying structure.
    solo = np.array([roughness(a["dist"].reshape(h // 2, w) if h % 2 == 0
                               else a["dist"].reshape(h, w // 2))
                     for a, _ in pairs[: args.limit]]).mean(axis=0)

    print(f"{'candidate':<28}{'total':>10}{'vertical':>11}{'horizontal':>12}")
    print("-" * 61)
    for name, (tot, vert, horz) in sorted(scores.items(), key=lambda kv: kv[1][0]):
        print(f"{name:<28}{tot:>10.1f}{vert:>11.1f}{horz:>12.1f}")
    print("-" * 61)
    print(f"{'(one sub-frame alone)':<28}{solo[0]:>10.1f}{solo[1]:>11.1f}{solo[2]:>12.1f}")

    best, runner = sorted(scores.items(), key=lambda kv: kv[1][0])[:2]
    margin = (runner[1][0] - best[1][0]) / best[1][0] * 100
    print(f"\nbest: {best[0]}  ({margin:.0f}% better than {runner[0]})")
    if margin < 10:
        print("WARNING: margin under 10% -- not decisive. Recapture against a "
              "scene with more structure (a hand or an angled surface close to "
              "the sensor), since a flat wall looks smooth under every "
              "candidate and cannot discriminate.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    cap = sub.add_parser("capture", help="record sub-frames from the board")
    cap.add_argument("--port", default="COM5")
    cap.add_argument("--baud", type=int, default=2_000_000)
    cap.add_argument("--seconds", type=float, default=20.0)
    cap.add_argument("--out", default=str(DEFAULT_CAPTURE))
    cap.set_defaults(func=capture)

    slv = sub.add_parser("solve", help="score candidate assemblies")
    slv.add_argument("--capture", default=str(DEFAULT_CAPTURE))
    slv.add_argument("--limit", type=int, default=50, help="pairs to average over")
    slv.set_defaults(func=solve)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
