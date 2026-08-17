#!/usr/bin/env python3
"""Live viewer for the TMF8829 binary frame stream.

Pairs with the 'b' binary streaming mode in lib/tmf8829_driver_arduino/
tmf8829_shim.cpp. See streamZoneFrameBinary() there for the authoritative
frame layout; the parser below mirrors it.

    pip install pyserial numpy opencv-python
    python tools/tmf8829_viewer.py --port COM5

Keys (in the viewer window) are forwarded to the board, so the whole session
can be driven from here rather than from a separate serial monitor -- only one
process can hold the COM port at a time:

    e  enable + firmware download        m  start measuring
    c  next configuration                s  stop
    b  toggle binary streaming           f  full-resolution ASCII dump
    q / ESC  quit viewer (board keeps running)

Typical first run: e, then c four times to reach 16x16, then m, then b.

--no-gui skips OpenCV entirely and just prints frame statistics, which is the
easier way to confirm the protocol works before adding rendering on top.
"""

from __future__ import annotations

import argparse
import os
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

FP_MODE_NAMES = {
    0: "8x8", 1: "8x8B", 2: "16x16", 3: "32x32", 4: "32x32s", 5: "48x32",
}


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

    def __init__(self) -> None:
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
                del self.text_lines[:-12]
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
        self.parser = FrameParser()
        self.assembler = SubFrameAssembler()
        self.raw_subframes = False   # set True to see unassembled halves
        self._lock = threading.Lock()
        self._latest: Frame | None = None
        # Not named _stop: Thread already has a private _stop() method that
        # join() calls internally, and shadowing it with an Event breaks close().
        self._stopping = threading.Event()

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
    forwarded = set("ecmsbdftauwxz+-h")
    mask_low_confidence = True
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
            history.append(d0.astype(np.uint16))
            conf_history.append(c0)
            del history[:-args.smooth]
            del conf_history[:-args.smooth]

        if latest is not None:
            dist, conf = latest.as_grid()
            if args.smooth > 1 and len(history) > 1:
                dist = np.median(np.stack(history), axis=0).astype(np.uint16)
                conf = np.median(np.stack(conf_history), axis=0).astype(np.uint8)

            # The device's zone order is a horizontal mirror of the scene:
            # confirmed 2026-08-13 with a hand at two known positions (scene
            # top-right -> grid top-left, scene bottom-right -> grid
            # bottom-left). Rows track correctly, columns invert. Flipping
            # here rather than in as_grid() keeps the raw device order intact
            # for the Stage 3b sub-frame work.
            if not args.raw_order:
                dist, conf = np.fliplr(dist), np.fliplr(conf)

            # Fixed normalisation range, never per-frame min/max: an
            # auto-scaled image breathes as the scene changes, which actively
            # misleads while debugging geometry.
            span = max(1, args.dist_max - args.dist_min)
            norm = np.clip((dist.astype(np.int32) - args.dist_min) / span, 0.0, 1.0)

            # Inverted so near=red / far=blue, matching the firmware's ANSI
            # grid, so the two can be compared directly.
            colour = cv2.applyColorMap((255 - norm * 255).astype(np.uint8),
                                       COLORMAPS[args.colormap])
            if mask_low_confidence:
                colour[conf < args.conf_min] = (80, 80, 80)

            # NEAREST, not the default INTER_LINEAR: smoothing would blur away
            # exactly the per-zone structure this exists to show.
            height, width = dist.shape
            canvas = cv2.resize(colour, (width * args.scale, height * args.scale),
                                interpolation=cv2.INTER_NEAREST)

            label = (f"{latest.mode_name}  {width}x{height}  "
                     f"n={latest.zone_count}  #{latest.frame_num}  {rate.fps:.1f}fps")
            if latest.is_subframe:
                label += "  SUB-FRAME (order unverified)"
            errors = reader.parser.header_crc_errors + reader.parser.payload_crc_errors
            if errors:
                label += f"  crc-err={errors}"
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
                    "   b   turn on binary streaming",
            ]):
                cv2.putText(canvas, line, (24, 96 + i * 28), cv2.FONT_HERSHEY_SIMPLEX,
                            0.52, (210, 210, 210), 1, cv2.LINE_AA)

        cv2.putText(canvas, label, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (255, 255, 255), 1, cv2.LINE_AA)

        # The board's own replies -- "Preconfig 71", "state=measure", "#Err..." --
        # pulled out of the bytes the frame scanner skipped. Without this you
        # cannot tell which config you cycled to without a second process on
        # the port, and only one process can hold it.
        for i, line in enumerate(reader.parser.text_lines[-2:]):
            cv2.putText(canvas, line[:88], (8, canvas.shape[0] - 56 + i * 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (120, 210, 140), 1, cv2.LINE_AA)

        # Key legend. Without it there is nothing on screen saying the board can
        # be driven from here at all -- nor that the window must be focused
        # first for OpenCV to see a key.
        hint = ("e enable  c config  m measure  s stop  d disable  b binary  f dump  t timing"
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
    parser.add_argument("--dist-max", type=int, default=3000,
                        help="mm mapped to the far end of the colour map")
    parser.add_argument("--conf-min", type=int, default=40,
                        help="below this confidence a zone renders flat gray "
                             "(matches TMF8829_CONFIDENCE_DISPLAY_THRESHOLD)")
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
    print(f"listening on {args.port} @ {args.baud} -- press 'b' on the board "
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
