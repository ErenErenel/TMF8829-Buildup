#!/usr/bin/env python3
"""Live viewer for the TMF8829 binary frame stream.

Pairs with the 'v' binary streaming mode in lib/tmf8829_driver_arduino/
tmf8829_shim.cpp. See streamZoneFrameBinary() there for the authoritative
frame layout; the parser below mirrors it.

    pip install pyserial numpy opencv-python
    python tools/tmf8829_viewer.py --port COM5

Keys (in the viewer window) are forwarded to the board, so the whole session
can be driven from here rather than from a separate serial monitor -- only one
process can hold the COM port at a time:

    e  enable + firmware download        m  start measuring
    c  next configuration                s  stop
    v  toggle binary streaming           f  full-resolution ASCII dump
    q / ESC  quit viewer (board keeps running)

Typical first run: e, then c four times to reach 16x16, then m, then v.

--no-gui skips OpenCV entirely and just prints frame statistics, which is the
easier way to confirm the protocol works before adding rendering on top.
"""

from __future__ import annotations

import argparse
import os
import re
import struct
import sys
import threading
import time
from dataclasses import dataclass

try:
    import serial  # pyserial
except ImportError:
    sys.exit("pyserial missing: pip install pyserial")

try:
    import numpy as np
except ImportError:
    sys.exit("numpy missing: pip install numpy")


SYNC = b"TMF8"
HEADER_SIZE = 22
MAX_ZONES = 1536          # 48x32, the device's full SPAD count
MAX_STRIDE = 8
SYSTICK_HZ = 125_000.0    # device tick used for the frame-rate readout

# Colour a zone fades toward as its confidence drops below the trust floor.
GRAY = (80, 80, 80)
# A zone that returned nothing at all. Deliberately near-black and distinct
# from GRAY: "no measurement" and "a measurement I do not fully trust" are
# different states, and conflating them is what made dropouts look like
# genuine near-field objects.
NO_RETURN_COLOUR = (28, 28, 28)
# Distance 0 with a real confidence is NOT a no-return -- see colourise(). Given
# a deliberately off-ramp colour (turbo has no magenta) because it means one of
# two things worth noticing, not one thing worth hiding.
ALIAS_COLOUR = (220, 60, 200)
# Height of the status strip appended below the depth image, and the minimum
# window width the footer's text and colour bar need to fit.
# Three stacked bands with real gaps: colour bar, board replies, key legend.
# At 92 the bar (image_h+16..+28) and the first reply line (baseline image_h+36)
# collided, so board text was drawn through the ramp.
FOOTER_H = 116
MIN_CANVAS_W = 640

FP_MODE_NAMES = {
    0: "8x8", 1: "8x8B", 2: "16x16", 3: "32x32", 4: "32x32s", 5: "48x32",
}

# Realistic maximum range per preset, in mm -- what the colour ramp should span.
#
# A ramp fixed at 11000mm is actively misleading outside 8x8 long range: at
# 48x32 nothing can legitimately read past ~2700mm, so three quarters of the
# palette is unreachable and the whole scene crushes into the red end. Worse,
# it implies reach the mode does not have.
#
# Sources, per entry, since the datasheet gives two different "maximum" numbers
# that mean different things:
#   Table 5 p.16 -- maximum DETECTION distance, 90% white card, centre pixel,
#     350 lux fluorescent. Where returns realistically stop. Used where the
#     table states it unambiguously for the mode.
#   Table 6 p.25 -- the configured unambiguous WINDOW (set by histogram_bins).
#     A hard ceiling: past it, distant objects alias back down. Used for the
#     high-accuracy presets, where the 64-bin window binds well before
#     detection does, and for plain 8x8 where Table 5's rows are ambiguous.
PRESET_MAX_MM = {
    64: 5700,   # 8x8                  Table 6 window
    65: 11000,  # 8x8 long range       Table 5, 2000k iterations
    66: 5700,   # 8x8 high accuracy    Table 6 window
    67: 5900,   # 16x16                Table 5, white card centre
    68: 1400,   # 16x16 high accuracy  Table 6 window (64 bins)
    69: 5000,   # 32x32                Table 5, white card centre
    70: 1400,   # 32x32 high accuracy  Table 6 window
    71: 2700,   # 48x32                Table 5, white card centre
    72: 1400,   # 48x32 high accuracy  Table 6 window
}

# Fallback when no "Preconfig NN" line has been seen yet -- the frame header
# carries fp_mode, which does NOT distinguish the long-range or high-accuracy
# variants (64/65/66 are all FP_8x8A). Assumes the default variant of each.
FP_MODE_MAX_MM = {0: 5700, 1: 5700, 2: 5900, 3: 5000, 4: 5000, 5: 2700}

PRECONFIG_RE = re.compile(r"Preconfig\s+(\d+)")


# --------------------------------------------------------------------------
# CRC-16/CCITT-FALSE  (poly 0x1021, init 0xFFFF, no reflection, no final xor)
# --------------------------------------------------------------------------
def _build_crc_table() -> list[int]:
    table = []
    for i in range(256):
        crc = i << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        table.append(crc)
    return table


_CRC_TABLE = _build_crc_table()


def crc16(data: bytes) -> int:
    """Table-driven, so a 4.6kB payload at 15fps stays negligible.

    A bitwise implementation here would be ~550k iterations/sec of pure Python
    and would show up against the render loop.
    """
    crc = 0xFFFF
    for byte in data:
        crc = ((crc << 8) & 0xFFFF) ^ _CRC_TABLE[((crc >> 8) ^ byte) & 0xFF]
    return crc


@dataclass
class Frame:
    fp_mode: int
    is_subframe: bool
    zone_count: int
    grid_w: int
    grid_h: int
    frame_num: int
    systick: int
    distance_mm: np.ndarray   # uint16, shape (zone_count,)
    confidence: np.ndarray    # uint8,  shape (zone_count,)

    @property
    def mode_name(self) -> str:
        return FP_MODE_NAMES.get(self.fp_mode, f"fp{self.fp_mode}")

    def as_grid(self) -> tuple[np.ndarray, np.ndarray]:
        """Reshape to (rows, cols), padding a partial last row.

        Sub-frames carry fewer zones than grid_w*grid_h and their true shape is
        not yet known (Stage 3b), so they are laid out at the full grid width
        and simply come out short -- deliberately not guessed at.
        """
        width = self.grid_w if self.grid_w > 0 else 8
        rows = (self.zone_count + width - 1) // width
        pad = rows * width - self.zone_count
        dist = np.concatenate([self.distance_mm, np.zeros(pad, np.uint16)])
        conf = np.concatenate([self.confidence, np.zeros(pad, np.uint8)])
        return dist.reshape(rows, width), conf.reshape(rows, width)


class FrameParser:
    """Resynchronising parser over a byte stream that also carries ASCII.

    The board's own '#Err' text, startup banners and 'f' dumps share the port.
    They are not escaped or framed -- the scanner simply steps over anything
    that does not validate as a frame header.
    """

    def __init__(self, on_text=None, text_history: int = 12) -> None:
        self._buf = bytearray()
        self.header_crc_errors = 0
        self.payload_crc_errors = 0
        self.frames_ok = 0
        # Bytes the scanner steps over are, by definition, not frame data --
        # they are the board's own ASCII (banners, "Preconfig 71", "#Err...").
        # Capturing them here is what lets the viewer show the board's replies
        # without a second process holding the port.
        self.text_lines: list[str] = []
        self._textbuf = bytearray()
        # text_lines is a ROLLING buffer sized for the viewer's 2-line display.
        # Anything that must not miss a line -- a watchdog looking for
        # "I3C bus recovered", say -- has to use on_text, which fires once per
        # line as it is parsed. Polling text_lines (or worse, its length) drops
        # lines silently as soon as the board repeats an error fast enough to
        # saturate the buffer; that is exactly how the 2026-08-19 soak missed
        # the recovery verdict.
        self._on_text = on_text
        self._text_history = text_history

    def _absorb_text(self, chunk: bytes) -> None:
        self._textbuf.extend(chunk)
        while True:
            nl = self._textbuf.find(b"\n")
            if nl < 0:
                break
            raw, self._textbuf = self._textbuf[:nl], self._textbuf[nl + 1:]
            line = "".join(chr(b) for b in raw if 32 <= b < 127).strip()
            if len(line) >= 3:      # skip stray printable bytes from binary payload
                self.text_lines.append(line)
                del self.text_lines[:-self._text_history]
                if self._on_text is not None:
                    self._on_text(line)
        del self._textbuf[:-512]    # bound it if a stream never contains a newline

    def feed(self, data: bytes):
        self._buf.extend(data)
        yield from self._drain()

    def _drain(self):
        buf = self._buf
        while True:
            index = buf.find(SYNC)
            if index < 0:
                # A sync can straddle two reads, so retain the last 3 bytes.
                if len(buf) > 3:
                    self._absorb_text(bytes(buf[: len(buf) - 3]))
                    del buf[: len(buf) - 3]
                return
            if index > 0:
                self._absorb_text(bytes(buf[:index]))
                del buf[:index]
            if len(buf) < HEADER_SIZE:
                return

            header = bytes(buf[:HEADER_SIZE])
            if crc16(header[:20]) != struct.unpack_from("<H", header, 20)[0]:
                # False sync -- most likely the pattern occurring inside pixel
                # data. Step one byte and rescan rather than trusting a length.
                self.header_crc_errors += 1
                del buf[:1]
                continue

            (_ver, flags, fp_mode, stride) = struct.unpack_from("<BBBB", header, 4)
            zone_count, grid_w, grid_h = struct.unpack_from("<HBB", header, 8)
            frame_num, systick = struct.unpack_from("<II", header, 12)

            if not (0 < zone_count <= MAX_ZONES) or not (0 < stride <= MAX_STRIDE):
                self.header_crc_errors += 1
                del buf[:1]
                continue

            payload_len = zone_count * stride
            total = HEADER_SIZE + payload_len + 2
            if len(buf) < total:
                return  # wait for the rest; header CRC already vouched for the length

            payload = bytes(buf[HEADER_SIZE:HEADER_SIZE + payload_len])
            want = struct.unpack_from("<H", buf, HEADER_SIZE + payload_len)[0]
            if crc16(payload) != want:
                self.payload_crc_errors += 1
                del buf[:1]
                continue

            raw = np.frombuffer(payload, dtype=np.uint8).reshape(zone_count, stride)
            distance = raw[:, 0].astype(np.uint16) | (raw[:, 1].astype(np.uint16) << 8)
            confidence = raw[:, 2].copy()

            del buf[:total]
            self.frames_ok += 1
            yield Frame(
                fp_mode=fp_mode,
                is_subframe=bool(flags & 0x01),
                zone_count=zone_count,
                grid_w=grid_w,
                grid_h=grid_h,
                frame_num=frame_num,
                systick=systick,
                distance_mm=distance,
                confidence=confidence,
            )


class SubFrameAssembler:
    """Joins the two halves of a 32x32 / 48x32 measurement into one frame.

    The device splits those modes across two frames: the one with sub_result
    clear carries the EVEN rows, the one with it set carries the ODD rows.
    Determined empirically 2026-08-13 (see tools/tmf8829_subframe.py) and
    confirmed independently at both 32x32 and 48x32.

    Runs in the reader thread rather than the render loop on purpose: the
    viewer's single-slot buffer drops frames when rendering falls behind, and
    dropping half a pair would make assembly impossible. Pairing needs every
    frame; display does not.
    """

    def __init__(self) -> None:
        self._held: Frame | None = None

    def feed(self, frame: Frame) -> Frame | None:
        """Return a full frame, or None while waiting for the other half."""
        if frame.zone_count == frame.grid_w * frame.grid_h:
            self._held = None
            return frame                      # 8x8 / 16x16: already complete

        held, self._held = self._held, frame
        # Only join genuinely adjacent halves of the same measurement -- a
        # dropped frame in between would silently fuse two different scenes.
        if (held is None
                or held.is_subframe == frame.is_subframe
                or frame.frame_num != held.frame_num + 1
                or held.zone_count != frame.zone_count):
            return None

        even, odd = (held, frame) if not held.is_subframe else (frame, held)
        h, w = frame.grid_h, frame.grid_w
        if even.zone_count * 2 != h * w:
            return None

        dist = np.empty(h * w, np.uint16)
        conf = np.empty(h * w, np.uint8)
        dist.reshape(h, w)[0::2, :] = even.distance_mm.reshape(h // 2, w)
        dist.reshape(h, w)[1::2, :] = odd.distance_mm.reshape(h // 2, w)
        conf.reshape(h, w)[0::2, :] = even.confidence.reshape(h // 2, w)
        conf.reshape(h, w)[1::2, :] = odd.confidence.reshape(h // 2, w)

        self._held = None
        return Frame(fp_mode=frame.fp_mode, is_subframe=False, zone_count=h * w,
                     grid_w=w, grid_h=h, frame_num=frame.frame_num,
                     systick=frame.systick, distance_mm=dist, confidence=conf)


def _port_contenders() -> list[str]:
    """Best-effort list of processes likely to be holding the COM port.

    Windows-only, invoked only on the port-open failure path. Names actual
    PIDs rather than saying "port busy": the observed failure mode (2026-08-17)
    was a duplicate viewer plus a forgotten `pio device monitor`, and the fix
    is knowing which process to kill. A system Python 3.12 exists on this
    machine, so a bare `python tools/tmf8829_viewer.py` outside .venv silently
    launches a second instance.
    """
    if sys.platform != "win32":
        return []
    import subprocess
    query = ("Get-CimInstance Win32_Process | "
             "Where-Object { $_.Name -match '^(python|pio|platformio)' -and "
             "$_.CommandLine -match 'tmf8829_|device +monitor|miniterm' } | "
             "ForEach-Object { '{0,6}  {1}' -f $_.ProcessId, $_.CommandLine }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", query],
                             capture_output=True, text=True, timeout=15)
    except Exception:
        return []
    # Exclude the parent as well: the venv's python.exe is a redirector that
    # spawns the real interpreter as a child, so the WMI listing shows this
    # very invocation twice under two PIDs.
    own = {str(os.getpid()), str(os.getppid())}
    return [ln for ln in out.stdout.splitlines()
            if ln.strip() and ln.split()[0] not in own]


class SerialReader(threading.Thread):
    """Reads and parses on its own thread, publishing only the newest frame.

    A single-slot buffer rather than a queue: if rendering falls behind we want
    to drop frames, never accumulate them. An unbounded queue here would show
    up as steadily growing display lag that looks like a slow link.
    """

    def __init__(self, port: str, baud: int) -> None:
        # daemon goes through Thread.__init__ rather than a class attribute:
        # Thread.daemon is a property, and a class attribute would shadow it
        # while leaving the real _daemonic flag unset.
        super().__init__(name="serial-reader", daemon=True)
        # Set when the serial link dies (read or write). The GUI turns it into
        # a red banner; headless mode exits. Never leave this silent: a viewer
        # with a dead link still draws its instruction panel and still echoes
        # "sent 'x'", which looks functional and cost a debugging session.
        self.link_error: str | None = None
        try:
            self._serial = serial.Serial(port, baud, timeout=0.05)
        except (OSError, ValueError, serial.SerialException) as exc:
            lines = [f"cannot open {port}: {exc}",
                     "Only one process can hold the port -- close any serial "
                     "monitor and any other viewer/capture instance first."]
            contenders = _port_contenders()
            if contenders:
                lines.append("likely holders (kill with:  taskkill /F /PID <pid>):")
                lines.extend("  " + c for c in contenders)
            raise SystemExit("\n".join(lines)) from exc
        try:
            # Windows-only; default OS buffers are small enough to drop bytes
            # at 70% link utilisation if this thread hiccups.
            self._serial.set_buffer_size(rx_size=1 << 20)
        except (AttributeError, NotImplementedError):
            pass
        # Preset is tracked from the board's own "Preconfig NN" reply because
        # the frame header cannot supply it: fp_mode does not distinguish the
        # long-range and high-accuracy variants (64/65/66 are all FP_8x8A), and
        # those have wildly different ranges -- 5700mm vs 11000mm vs 5700mm.
        #
        # Via on_text, NOT by polling parser.text_lines: that is a rolling
        # 12-entry buffer, so a scan of it silently misses lines as soon as the
        # board prints faster than the display consumes them.
        self.preset: int | None = None
        self.parser = FrameParser(on_text=self._note_text)
        self.assembler = SubFrameAssembler()
        self.raw_subframes = False   # set True to see unassembled halves
        self._lock = threading.Lock()
        self._latest: Frame | None = None
        # Not named _stop: Thread already has a private _stop() method that
        # join() calls internally, and shadowing it with an Event breaks close().
        self._stopping = threading.Event()

    def _note_text(self, line: str) -> None:
        """Fires once per board ASCII line, on the reader thread."""
        m = PRECONFIG_RE.search(line)
        if m:
            self.preset = int(m.group(1))

    def resolved_max_mm(self, fp_mode: int | None) -> tuple[int, str]:
        """(range_mm, provenance) for the colour ramp's far end."""
        if self.preset is not None and self.preset in PRESET_MAX_MM:
            return PRESET_MAX_MM[self.preset], f"preconfig {self.preset}"
        if fp_mode is not None and fp_mode in FP_MODE_MAX_MM:
            return FP_MODE_MAX_MM[fp_mode], f"{FP_MODE_NAMES.get(fp_mode, '?')} default"
        return 11000, "unknown mode"

    def run(self) -> None:
        while not self._stopping.is_set():
            try:
                pending = self._serial.in_waiting or 1
                data = self._serial.read(pending)
            except (OSError, serial.SerialException) as exc:
                self.link_error = str(exc)
                print(f"serial read failed: {exc}", file=sys.stderr)
                break
            if not data:
                continue
            for frame in self.parser.feed(data):
                if not self.raw_subframes:
                    frame = self.assembler.feed(frame)
                    if frame is None:
                        continue      # holding one half, waiting for the other
                with self._lock:
                    self._latest = frame

    def take(self) -> Frame | None:
        with self._lock:
            frame, self._latest = self._latest, None
            return frame

    def send_key(self, key: str) -> None:
        try:
            self._serial.write(key.encode())
        except (OSError, serial.SerialException) as exc:
            self.link_error = str(exc)

    def close(self) -> None:
        self._stopping.set()
        self.join(timeout=1.0)
        self._serial.close()


class FrameRate:
    """Frame rate from the device's own 125kHz tick, not host arrival times."""

    def __init__(self) -> None:
        self._last: int | None = None
        self.fps = 0.0

    def update(self, systick: int) -> None:
        if self._last is not None:
            delta = (systick - self._last) & 0xFFFFFFFF
            if 0 < delta < SYSTICK_HZ * 5:
                instant = SYSTICK_HZ / delta
                self.fps = instant if self.fps == 0 else 0.8 * self.fps + 0.2 * instant
        self._last = systick


class SpanTracker:
    """Chooses the mm range mapped across the colour ramp.

    Fixed by default, for the reason that has always applied: a range that
    follows the scene makes the image breathe, and while checking geometry a
    breathing image actively misleads.

    But a fixed range only works when it matches the scene. Measured on a real
    desk scene, p5..p95 of the trustworthy data was 75..1636mm against the
    100..3000mm default -- half the palette unused, everything squeezed into
    the warm end and hard to tell apart. --dist-auto fits the range to the
    scene's percentiles instead, with two guards against breathing:
    low-confidence and no-return zones are excluded from the fit (they are
    exactly the noisy ones that would jerk the range around), and the range
    only moves once a candidate has persisted for several seconds and differs
    enough to matter.
    """

    def __init__(self, fixed_lo: int, fixed_hi: int, auto: bool,
                 settle_s: float = 3.0, min_change: float = 0.15) -> None:
        self.lo, self.hi = fixed_lo, fixed_hi
        self.auto = auto
        self._settle_s = settle_s
        self._min_change = min_change
        self._cand: tuple[int, int] | None = None
        self._cand_since = 0.0

    def update(self, dist, conf, no_return) -> tuple[int, int]:
        if not self.auto:
            return self.lo, self.hi

        good = (~no_return) & (conf >= 8)      # 8 ~= the floor of genuine far returns
        if good.sum() < 16:                    # too little to fit; keep the last range
            return self.lo, self.hi

        vals = dist[good]
        lo = int(np.percentile(vals, 2))
        hi = int(np.percentile(vals, 98))
        if hi - lo < 100:                      # a flat scene still needs a usable ramp
            hi = lo + 100

        now = time.monotonic()
        span = max(1, self.hi - self.lo)
        moved = (abs(lo - self.lo) + abs(hi - self.hi)) / span
        if moved < self._min_change:
            self._cand = None
            return self.lo, self.hi

        if self._cand is None or abs(lo - self._cand[0]) + abs(hi - self._cand[1]) > span * 0.1:
            self._cand, self._cand_since = (lo, hi), now
        elif now - self._cand_since >= self._settle_s:
            self.lo, self.hi = self._cand
            self._cand = None
        return self.lo, self.hi


def smooth_valid(hist, conf_hist):
    """Temporal median over VALID samples only -> (dist, conf).

    A plain median across the stack includes no-return zeros, which drags a
    zone that flickers toward 0 even when most frames in the window carry real
    data -- so an intermittent far return reads as nearer than it is, or
    vanishes entirely. Zeros carry no distance information, so they are
    excluded from the median rather than averaged into it.

    A zone with no valid sample anywhere in the window stays 0/0 and is
    reported as a genuine no-return, which is the honest answer for it.
    """
    d = np.stack(hist).astype(np.float32)
    c = np.stack(conf_hist).astype(np.float32)

    valid = (d > 0) & (c > 0)
    d[~valid] = np.nan
    c[~valid] = np.nan

    # Seed all-invalid zones with a real 0 so nanmedian never sees an all-NaN
    # slice (which warns and returns NaN). Cheaper and clearer than suppressing
    # the warning, and it lands on exactly the value those zones should have.
    dead = ~valid.any(axis=0)
    d[0][dead] = 0.0
    c[0][dead] = 0.0

    return (np.nanmedian(d, axis=0).astype(np.uint16),
            np.nanmedian(c, axis=0).astype(np.uint8))


def colourise(dist, conf, lo: int, hi: int, args, colormap: int,
              *, shade: bool = True, no_return=None):
    """Distance grid -> BGR image. Shared by the GUI and any offline tooling,
    so a rendering question can be answered without opening a window."""
    import cv2

    span = max(1, hi - lo)
    norm = np.clip((dist.astype(np.int32) - lo) / span, 0.0, 1.0)
    gamma = getattr(args, "dist_gamma", 1.0)
    if gamma != 1.0:
        norm = norm ** gamma

    # Inverted so near=red / far=blue, matching the firmware's ANSI grid, so
    # the two can be compared directly.
    colour = cv2.applyColorMap((255 - norm * 255).astype(np.uint8), colormap)

    if shade and args.conf_mode != "off":
        # Confidence falls as ~1/distance (measured: median 128 @250mm, 42
        # @750mm, 7 @5000mm -- 3x and 20x in range give 3.0x and 18x in
        # confidence). So a FIXED threshold cannot work at any setting: tuned
        # for the near field it deletes the far field, tuned for the far field
        # it accepts noise up close. At the old fixed 40 this discarded 18.4%
        # of real measurements -- half of everything at 1-1.5m, all beyond 4m.
        if args.conf_mode == "adaptive":
            floor = np.maximum(args.conf_floor, args.conf_k / np.maximum(dist, 1))
        else:                                    # "fixed"
            floor = np.full(dist.shape, float(args.conf_min))

        # Shade rather than delete. A hard cutoff is what made marginal data
        # look like holes in the scene; fading toward gray keeps the geometry
        # visible while still showing that it is not fully trusted.
        q = np.clip(conf.astype(np.float64) / np.maximum(floor, 1e-6),
                    0.0, 1.0)[..., None]
        colour = (colour * q + np.float64(GRAY) * (1.0 - q)).astype(np.uint8)

    # Must come last: a zone reporting distance 0 lands at the NEAR end of the
    # ramp -- bright red, reading as "object touching the sensor" -- so the
    # zero cases have to be painted explicitly, and stay distinct with shading
    # off.
    #
    # AND, not OR. A genuine no-return reports BOTH fields zero (measured:
    # min=max=0 across every such sample). Distance 0 with a real confidence is
    # a different thing entirely, and there are two of them: an object at the
    # 10mm minimum range, or a DISTANT object aliased onto 0mm -- datasheet
    # 7.7.2, "for the vcsel_period default setting of 19.72 MHz VCSEL clock, an
    # object at 7.6m is aliased to 0m". Folding both into the no-return mask
    # with OR painted aliased objects as absent and inflated the no-return
    # statistic by an unmeasured amount.
    if no_return is None:
        no_return = (dist == 0) & (conf == 0)
    colour[no_return] = NO_RETURN_COLOUR
    colour[(dist == 0) & ~no_return] = ALIAS_COLOUR
    return colour


def draw_colourbar(canvas, lo: int, hi: int, colormap: int, *,
                   x: int, y: int, w: int, h: int, gamma: float = 1.0) -> None:
    """Colour ramp with mm labels.

    Without it the image shows relative depth only -- there is no way to read a
    distance off it, which matters more once --dist-auto can move the range.
    """
    # Imported here, not at module scope: --no-gui must keep working on a host
    # with no opencv, which is why run_gui() imports it lazily too.
    import cv2

    # Never assume the canvas is wide enough: an 8x8 grid at the default scale
    # is only 192px across, narrower than the bar's natural width.
    w = max(40, min(w, canvas.shape[1] - x - 8))
    h = max(4, min(h, canvas.shape[0] - y - 2))
    ramp = np.linspace(255, 0, w, dtype=np.uint8)[None, :].repeat(h, 0)
    canvas[y:y + h, x:x + w] = cv2.applyColorMap(ramp, colormap)
    cv2.rectangle(canvas, (x, y), (x + w, y + h), (255, 255, 255), 1)
    for frac in (0.0, 0.5, 1.0):
        # Position along the bar is a colour-ramp fraction, so invert the gamma
        # to get the distance it stands for -- otherwise the midpoint label is
        # simply wrong whenever the mapping is not linear.
        mm = int(lo + (frac ** (1.0 / gamma) if gamma != 1.0 else frac) * (hi - lo))
        tx = int(x + frac * w)
        cv2.putText(canvas, f"{mm}", (max(x, tx - 14), y - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.36, (215, 215, 215), 1, cv2.LINE_AA)


def run_headless(reader: SerialReader) -> None:
    rate = FrameRate()
    last_report = 0.0
    while True:
        if reader.link_error:
            sys.exit(f"serial link lost: {reader.link_error}")
        frame = reader.take()
        if frame is None:
            time.sleep(0.005)
            continue
        rate.update(frame.systick)
        now = time.monotonic()
        if now - last_report < 0.5:
            continue
        last_report = now
        valid = frame.distance_mm[frame.distance_mm > 0]
        print(
            f"{frame.mode_name:>6} "
            f"{'SUB ' if frame.is_subframe else '    '}"
            f"n={frame.zone_count:<5} grid={frame.grid_w}x{frame.grid_h} "
            f"#{frame.frame_num:<7} {rate.fps:5.1f}fps  "
            f"dist min/med/max={valid.min() if valid.size else 0}/"
            f"{int(np.median(valid)) if valid.size else 0}/"
            f"{valid.max() if valid.size else 0}mm  "
            f"conf max={frame.confidence.max()}  "
            f"err hdr={reader.parser.header_crc_errors} "
            f"pay={reader.parser.payload_crc_errors}"
        )


def run_gui(reader: SerialReader, args) -> None:
    try:
        import cv2
    except ImportError:
        sys.exit("opencv missing: pip install opencv-python (or use --no-gui)")

    # TURBO is the default: it is the one designed for depth/scientific data,
    # with more distinguishable steps than JET and without JET's misleading
    # bright band in the middle. The others are perceptually uniform and
    # colourblind-safer, at the cost of fewer distinguishable levels.
    COLORMAPS = {"turbo": cv2.COLORMAP_TURBO, "jet": cv2.COLORMAP_JET,
                 "inferno": cv2.COLORMAP_INFERNO, "viridis": cv2.COLORMAP_VIRIDIS,
                 "magma": cv2.COLORMAP_MAGMA, "plasma": cv2.COLORMAP_PLASMA}

    window = "TMF8829"
    cv2.namedWindow(window, cv2.WINDOW_AUTOSIZE)
    rate = FrameRate()
    # Every vendor app key plus the shim's own, except '#' (reset) which is too
    # easy to hit by accident. 'd' disables the device -- EN low.
    # 'b' is deliberately NOT forwarded: on the board it enters the vendor app's
    # binary input mode, which then swallows every following byte as command
    # payload and needs a full valid command to leave. Our stream toggle is 'v'.
    forwarded = set("ecmsvdftauwxz+-h")
    mask_low_confidence = True
    # None until a frame arrives, so the ramp can follow the active mode.
    span_tracker = SpanTracker(args.dist_min, args.dist_max or 11000, args.dist_auto)
    cur_lo, cur_hi = args.dist_min, args.dist_max or 11000
    range_note = "fixed" if args.dist_max else "unknown mode"
    latest: Frame | None = None
    sent_key: str | None = None
    sent_at = 0.0

    # Temporal smoothing. Measured on real 48x32 data: per-zone frame-to-frame
    # noise has a median of ~116mm and a 90th percentile of ~1481mm. That upper
    # tail is not drift -- those zones flip between two different surfaces
    # (an edge zone seeing a near object one frame, the wall behind it the
    # next). Hence MEDIAN rather than mean or an exponential average: averaging
    # a 200mm and a 2500mm reading yields 1350mm, pointing at empty space where
    # there is no surface at all, whereas a median picks one of the two real
    # ones. Noise scales with resolution (a 48x32 zone collects ~1/24th the
    # photons of an 8x8 zone), so this matters most exactly where it is enabled.
    history: list[np.ndarray] = []
    conf_history: list[np.ndarray] = []

    while True:
        frame = reader.take()
        if frame is not None:
            latest = frame
            rate.update(frame.systick)

        if frame is not None and args.smooth > 1:
            d0, c0 = frame.as_grid()
            # The grid changes shape whenever 'c' cycles to a different
            # preconfig, and a window holding two resolutions cannot be
            # stacked -- np.stack raises and takes the viewer down with it.
            # The old frames describe a different geometry anyway, so there is
            # nothing to salvage: drop the window and refill it.
            if history and history[-1].shape != d0.shape:
                history.clear()
                conf_history.clear()
            history.append(d0.astype(np.uint16))
            conf_history.append(c0)
            del history[:-args.smooth]
            del conf_history[:-args.smooth]

        if latest is not None:
            dist, conf = latest.as_grid()
            if args.smooth > 1 and len(history) > 1:
                dist, conf = smooth_valid(history, conf_history)

            # The device's zone order is a horizontal mirror of the scene:
            # confirmed 2026-08-13 with a hand at two known positions (scene
            # top-right -> grid top-left, scene bottom-right -> grid
            # bottom-left). Rows track correctly, columns invert. Flipping
            # here rather than in as_grid() keeps the raw device order intact
            # for the Stage 3b sub-frame work.
            if not args.raw_order:
                dist, conf = np.fliplr(dist), np.fliplr(conf)

            # A zone that returned nothing always reports distance 0 AND
            # confidence 0 (measured: min=max=0 across every such sample). It
            # carries no information, so it must not be coloured as though it
            # did -- distance 0 lands at the NEAR end of the ramp, i.e. bright
            # red, reading as "object touching the sensor". Tracked separately
            # so it stays distinct even with confidence shading turned off.
            #
            # AND rather than OR: distance 0 with a real confidence is an
            # object at minimum range or an aliased distant one, not an absent
            # return. See colourise() for the datasheet reference.
            #
            # Safe to evaluate AFTER smoothing only because smooth_valid()
            # medians over valid samples. A plain median across the stack
            # returns 0 for any zone that reported real data in a minority of
            # the window, which manufactured no-returns out of precisely the
            # intermittent far returns worth seeing -- and at 5.4fps a 5-frame
            # window spans nearly a second.
            no_return = (dist == 0) & (conf == 0)

            # Follow the active mode unless --dist-max pinned it. Each preset
            # has its own reach (48x32 stops ~2700mm, 8x8 long range reaches
            # 11000mm), so one fixed ramp either wastes most of the palette or
            # overstates what the mode can see. --dist-auto still wins: an
            # explicit request to fit the scene outranks a table lookup.
            if args.dist_max is None and not args.dist_auto:
                span_tracker.hi, range_note = reader.resolved_max_mm(latest.fp_mode)

            lo, hi = span_tracker.update(dist, conf, no_return)
            colour = colourise(dist, conf, lo, hi, args,
                               COLORMAPS[args.colormap],
                               shade=mask_low_confidence,
                               no_return=no_return)

            # NEAREST, not the default INTER_LINEAR: smoothing would blur away
            # exactly the per-zone structure this exists to show.
            height, width = dist.shape
            canvas = cv2.resize(colour, (width * args.scale, height * args.scale),
                                interpolation=cv2.INTER_NEAREST)

            cur_lo, cur_hi = lo, hi
            label = (f"{latest.mode_name}  {width}x{height}  "
                     f"n={latest.zone_count}  #{latest.frame_num}  {rate.fps:.1f}fps")
            if latest.is_subframe:
                label += "  SUB-FRAME (order unverified)"
            errors = reader.parser.header_crc_errors + reader.parser.payload_crc_errors
            if errors:
                label += f"  crc-err={errors}"
            dropped = float(no_return.mean()) * 100.0
            if dropped >= 0.5:
                label += f"  no-return {dropped:.0f}%"
            # Reported separately because it is a different phenomenon and a
            # rising count is a signal in itself: with dithering off, these are
            # candidates for the 7.6m alias rather than contact-range objects.
            aliased = float(((dist == 0) & ~no_return).mean()) * 100.0
            if aliased >= 0.5:
                label += f"  zero-dist {aliased:.0f}%"
        else:
            # No frame yet -- the common case right after a flash, since that
            # resets the board and leaves the device disabled. Draw the same
            # furniture anyway: a blank window with no legend gives the user
            # nothing to act on, which is exactly when they need it most.
            canvas = np.full((360, 760, 3), 32, np.uint8)
            label = "waiting for frames"
            for i, line in enumerate([
                    "No binary frames arriving yet.",
                    "",
                    "Click this window first, then:",
                    "   e   enable device + download firmware  (~4 s)",
                    "   s   stop, then c until the mode you want",
                    "   m   start measuring",
                    "   v   turn on binary streaming",
            ]):
                cv2.putText(canvas, line, (24, 96 + i * 28), cv2.FONT_HERSHEY_SIMPLEX,
                            0.52, (210, 210, 210), 1, cv2.LINE_AA)

        # Footer strip. Status text used to be drawn straight onto the depth
        # image, covering the bottom two rows of zones; giving it its own space
        # keeps every zone visible and leaves room for the colour bar.
        #
        # The image itself can be narrower than the text that describes it --
        # an 8x8 grid at the default scale is 192px wide, against ~600px of key
        # legend -- so pad to a width the footer can actually use.
        image_h, image_w = canvas.shape[0], canvas.shape[1]
        if canvas.shape[1] < MIN_CANVAS_W:
            # Filler was (24,24,24) against a NO_RETURN_COLOUR of (28,28,28) --
            # indistinguishable, so at 16x16 (384px of image in a 640px canvas)
            # a quarter of the window looked like dead zones. Darker filler
            # plus an explicit border makes the sensor's actual extent obvious.
            canvas = np.hstack([
                canvas,
                np.full((image_h, MIN_CANVAS_W - canvas.shape[1], 3), 8, np.uint8)])
        canvas = np.vstack([canvas,
                            np.full((FOOTER_H, canvas.shape[1], 3), 8, np.uint8)])
        if image_w < canvas.shape[1]:
            cv2.rectangle(canvas, (0, 0), (image_w, image_h - 1), (90, 90, 90), 1)

        cv2.putText(canvas, label, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (255, 255, 255), 1, cv2.LINE_AA)

        # Without a scale the image shows relative depth only -- and once
        # --dist-auto can move the range, "red" no longer means a fixed
        # distance at all, so the numbers are the only way to read it.
        draw_colourbar(canvas, cur_lo, cur_hi, COLORMAPS[args.colormap],
                       x=8, y=image_h + 14, w=220, h=12, gamma=args.dist_gamma)
        legend = f"{args.conf_mode} conf"
        # Say where the far end of the ramp came from. Without it the bar is a
        # bare number and there is no way to tell a mode-derived limit from a
        # pinned one -- which matters, because the two mean different things
        # about a black zone at the top of the range.
        if args.dist_auto:
            legend += " | auto range"
        else:
            legend += f" | range: {range_note}"
        if args.dist_gamma != 1.0:
            legend += f" | gamma {args.dist_gamma:g}"
        if not mask_low_confidence:
            legend += " | UNSHADED"
        cv2.putText(canvas, legend, (238, image_h + 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (170, 170, 170), 1, cv2.LINE_AA)

        # The board's own replies -- "Preconfig 71", "state=measure", "#Err..." --
        # pulled out of the bytes the frame scanner skipped. Without this you
        # cannot tell which config you cycled to without a second process on
        # the port, and only one process can hold it.
        for i, line in enumerate(reader.parser.text_lines[-2:]):
            cv2.putText(canvas, line[:88], (8, canvas.shape[0] - 58 + i * 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (120, 210, 140), 1, cv2.LINE_AA)

        # Key legend. Without it there is nothing on screen saying the board can
        # be driven from here at all -- nor that the window must be focused
        # first for OpenCV to see a key.
        hint = ("e enable  c config  m measure  s stop  d disable  v binary  f dump  t timing"
                "   |   M mask  q quit")
        cv2.putText(canvas, hint, (8, canvas.shape[0] - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (200, 200, 200), 1, cv2.LINE_AA)

        # Confirm a forwarded keystroke, so a press that went to the terminal
        # instead of this window is visibly distinguishable from one that landed.
        if sent_key and time.monotonic() - sent_at < 1.2 and not reader.link_error:
            cv2.putText(canvas, f"sent '{sent_key}'", (canvas.shape[1] - 130, 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (60, 220, 255), 1, cv2.LINE_AA)

        # A dead link must be unmissable. Without this the window keeps drawing
        # its instruction panel and echoing keys, which looks functional while
        # nothing reaches the board (the 2026-08-17 duplicate-viewer incident).
        if reader.link_error:
            cv2.rectangle(canvas, (0, 30), (canvas.shape[1], 94), (30, 30, 170), -1)
            cv2.putText(canvas, "SERIAL LINK LOST -- keys are NOT reaching the board",
                        (12, 56), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (255, 255, 255), 2, cv2.LINE_AA)
            cv2.putText(canvas, f"{reader.link_error[:80]}  --  quit (q) and restart",
                        (12, 84), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                        (220, 220, 255), 1, cv2.LINE_AA)

        cv2.imshow(window, canvas)

        # Must be called every iteration or the window never repaints.
        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27):
            break
        if key == ord("M"):
            mask_low_confidence = not mask_low_confidence
        elif key != 255 and chr(key) in forwarded and not reader.link_error:
            reader.send_key(chr(key))
            sent_key, sent_at = chr(key), time.monotonic()

    cv2.destroyAllWindows()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", default="COM5")
    parser.add_argument("--baud", type=int, default=2_000_000,
                        help="must match UART_BAUD_RATE in src/main.cpp")
    parser.add_argument("--dist-min", type=int, default=100,
                        help="mm mapped to the near end of the colour map")
    parser.add_argument("--dist-max", type=int, default=None,
                        help="mm mapped to the far end of the colour map. "
                             "By default this FOLLOWS THE ACTIVE PRESET -- "
                             "2700mm at 48x32, 5900mm at 16x16, 11000mm at 8x8 "
                             "long range (datasheet Tables 5 and 6), so the "
                             "ramp spans what the current mode can actually "
                             "reach. Pass a value to pin it instead, which is "
                             "what you want when comparing two modes directly")
    parser.add_argument("--dist-gamma", type=float, default=1.0,
                        help="curve on the distance->colour mapping. 1.0 is "
                             "linear. Below 1.0 gives the near field more of "
                             "the ramp while still spanning the full range -- "
                             "useful because a linear 0-11000mm map compresses "
                             "a 0-2m indoor scene into the bottom fifth")
    parser.add_argument("--conf-min", type=int, default=40,
                        help="confidence floor for --conf-mode fixed "
                             "(matches TMF8829_CONFIDENCE_DISPLAY_THRESHOLD)")
    parser.add_argument("--conf-mode", default="adaptive",
                        choices=["adaptive", "fixed", "off"],
                        help="how confidence shades a zone. adaptive (default) "
                             "scales the floor as conf_k/distance, because "
                             "confidence falls as ~1/d; fixed uses --conf-min "
                             "everywhere (deletes far data); off ignores "
                             "confidence entirely")
    parser.add_argument("--conf-k", type=float, default=15000.0,
                        help="adaptive floor = conf_k / distance_mm. 15000 "
                             "tracks the measured 10th percentile of genuine "
                             "returns, so ~90%% of real data stays fully "
                             "coloured at every range")
    parser.add_argument("--conf-floor", type=float, default=3.0,
                        help="absolute lower bound on the adaptive floor, so "
                             "very distant zones still need some signal")
    parser.add_argument("--dist-auto", action=argparse.BooleanOptionalAction,
                        default=False,
                        help="fit the colour range to the scene (2nd..98th "
                             "percentile of trusted zones) instead of using "
                             "--dist-min/--dist-max. OFF by default: a fixed "
                             "absolute scale is what lets you see the sensor's "
                             "real reach and compare one session to another. "
                             "Turn it on to pull detail out of a narrow scene")
    parser.add_argument("--scale", type=int, default=24,
                        help="pixels per zone")
    parser.add_argument("--no-gui", action="store_true",
                        help="print frame statistics instead of rendering")
    parser.add_argument("--smooth", type=int, default=5, metavar="N",
                        help="temporal median over the last N frames (1 = off). "
                             "Median, not mean: noisy zones flip between two "
                             "surfaces, and a mean lands between them")
    parser.add_argument("--colormap", default="turbo",
                        choices=["turbo", "jet", "inferno", "viridis", "magma", "plasma"])
    parser.add_argument("--raw-order", action="store_true",
                        help="skip the horizontal mirror and show zones in raw "
                             "device order (for sub-frame/ordering work)")
    args = parser.parse_args()

    reader = SerialReader(args.port, args.baud)
    reader.raw_subframes = args.raw_order
    reader.start()
    print(f"listening on {args.port} @ {args.baud} -- press 'v' on the board "
          f"to start binary streaming")
    try:
        if args.no_gui:
            run_headless(reader)
        else:
            run_gui(reader, args)
    except KeyboardInterrupt:
        pass
    finally:
        reader.close()


if __name__ == "__main__":
    main()
