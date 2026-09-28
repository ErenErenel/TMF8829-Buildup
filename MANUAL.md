# TMF8829-Buildup — operating manual

Reference for the serial link and the Python tools. For *why* anything is built
this way, see `CLAUDE.md`.

> Only one process can hold COM5 at a time. Close the monitor before starting
> the viewer, and vice versa.

---

## Keys

Single characters, no Enter. Works in the monitor, or in the viewer window once
you click it to give it focus.

| Key | Action |
|---|---|
| `e` | enable device + download firmware — **always first** |
| `c` | next configuration (only while stopped) |
| `m` | start measuring |
| `s` | stop measuring |
| `d` | disable device (drives EN low) |
| `v` | toggle binary streaming *(shim)* |
| `f` | dump last frame at full resolution *(shim)* |
| `t` | toggle per-sub-frame timing report *(shim)* — prints `#T,n=..,bus=..,emit=..,period=..` every 30 sub-frames, in µs |
| `+` / `-` | log level up / down — **leave at default** |
| `a` `h` `p` `u` `w` `x` `z` `#` | registers · help · power down · get config · wake · clock corr · histogram · reset |
| `b` | **enters binary input mode — see warning below** |

> ⚠️ **`b` is not the streaming toggle — that is `v`.** `b` puts the vendor app
> into binary input mode, where every following byte is command payload, not a
> key. Send an invalid identifier (anything but `0x31`/`0x32`) and it prints
> `#Err,BinaryCmd,..` and exits. Send a *valid* one and it waits for the full
> payload — 190 bytes for `0x31` — so `b` followed by `1` looks like a hang
> until those bytes arrive. The viewer does not forward `b` for this reason.
> It is the only way to set measurement period / iterations; see CLAUDE.md.

**To disable without serial:** press the black `B1`/NRST button. EN drops and
the sensor stays off until you send `e`.

---

## Configurations

`c` cycles these. It is ignored unless stopped, so send `s` first.
`configNr` is **not** reset by `e` — read back the `Preconfig NN` line rather
than counting keypresses.

| Preconfig | Mode | Frame ID | Zones/frame | Cadence |
|---|---|---|---|---|
| 64 | 8×8 | 16 | 64 | 33 ms |
| 65 | 8×8 long range | 16 | 64 | 33 ms |
| 66 | 8×8 high accuracy | 16 | 64 | 33 ms |
| 67 | **16×16** | 18 | 256 | 33 ms |
| 68 | 16×16 high accuracy | 18 | 256 | 33 ms |
| 69 | **32×32** | 19 | 512 ×2 | 66 ms |
| 70 | 32×32 high accuracy | 19 | 512 ×2 | 66 ms |
| 71 | **48×32** | 21 | 768 ×2 | 66 ms |
| 72 | 48×32 high accuracy | 21 | 768 ×2 | 66 ms |

“×2” = split across two sub-frames; the viewer reassembles them.

**Startup sequence:** `e` → `s` → `c` until the target Preconfig → `m` → `v`

---

## Commands

Flash — `pio` is not on PATH; use the PlatformIO Core CLI terminal.

Upload copies to the **ST-Link's mbed virtual disk** (`upload_protocol = mbed`).
Pass **no** `--upload-port` — it auto-detects `D:` (`NOD_H563ZI`); handing it
`COM5` makes it try to write `COM5\firmware.bin`. Success looks like
`Auto-detected: D:\` → `Firmware has been successfully uploaded.`

```powershell
pio run --target upload
```

If it fails with **"Please specify `upload_port`"** and `D:` is missing, the USB
mass-storage driver has wedged (CLAUDE.md, 08-17/08-18) — fix the driver, don't
work around it. Meanwhile the SWD path still flashes, and works even while
something holds COM5: uncomment the `custom`/`upload_command` pair in
`platformio.ini`. There, `programmed 0 bytes ... identical` on an unchanged
build is a verify-skip, not a failure.

Serial monitor — `-f direct` is required or ANSI colour renders as escape text.
Baud must match `UART_BAUD_RATE` in `src/main.cpp`.

```powershell
pio device monitor -p COM5 -b 2000000 -f direct
```

Live viewer.

```powershell
.\.venv\Scripts\python.exe tools\tmf8829_viewer.py
```

Link check without the GUI — want `err hdr=0 pay=0`.

```powershell
.\.venv\Scripts\python.exe tools\tmf8829_viewer.py --no-gui
```

Long unattended run. The bus wedges after ~16-25 min; this detects it, replays
the recovery, and keeps going, logging the clean interval before each wedge to
`.captures/`. Run `--self-test` first — it exercises the recovery path once and
exits, so you find out it works *before* leaving it alone for hours.

```powershell
.\.venv\Scripts\python.exe tools\tmf8829_soak.py --self-test
.\.venv\Scripts\python.exe tools\tmf8829_soak.py --minutes 480 --preconfig 71
```

Record 20 seconds to `.captures/`.

```powershell
.\.venv\Scripts\python.exe tools\tmf8829_subframe.py capture --seconds 20
```

Re-verify the sub-frame interleave (32×32 or 48×32 only).

```powershell
.\.venv\Scripts\python.exe tools\tmf8829_subframe.py solve
```

### Viewer options

`--port COM5` · `--baud 2000000` · `--smooth 5` (temporal median, `1` = off) ·
`--colormap turbo|jet|inferno|viridis|magma|plasma` · `--scale 24` ·
`--raw-order` · `--no-gui`

**Colour range.** Fixed by default, **100–11000 mm** — the datasheet's maximum
detection range (Table 5, p.16), so the ramp spans what the sensor can actually
do and a colour means the same distance in every session. The colour bar under
the image is labelled in mm.

A linear 0–11 m ramp squeezes a 0–2 m indoor scene into the bottom fifth. Two
ways to get near-field detail back without giving up the absolute scale:

```powershell
--dist-gamma 0.5      # curve the ramp: near field gets more of it, still 0-11m
--dist-max 3000       # or just narrow the range
--dist-auto           # or fit the range to the scene (off by default)
```

**Confidence.** `--conf-mode adaptive` (default) fades a zone toward gray as its
confidence drops below `--conf-k / distance` (k=15000). Adaptive because
confidence falls as ~1/distance — a *fixed* threshold deletes the far field
(the old `--conf-min 40` hid 18% of real measurements, and everything past 4 m).
`--conf-mode fixed` restores the old behaviour, `off` ignores confidence.

**Reading the image:** near-black zones returned nothing at all (distance and
confidence both 0). Washed-out/gray zones did measure something, but weakly —
the paler the less trusted. Fully saturated colour means a confident reading.

In the window: `q`/Esc quits, `M` toggles confidence shading, `e c m s v f`
forward to the board.

---

## If the board seems deaf

In order — the first two caused a full debugging session on 08-17:

1. **Check for duplicate port holders.** Only one process can hold COM5. The
   viewer refuses to start (naming the PIDs) if the port is taken, and shows a
   red **SERIAL LINK LOST** banner if it loses the port while running — but a
   forgotten monitor or a second viewer can still be sitting on the port:

   ```powershell
   Get-CimInstance Win32_Process |
     Where-Object { $_.CommandLine -match 'tmf8829_|device monitor' } |
     Select-Object ProcessId, CommandLine
   # kill extras:  taskkill /F /PID <pid>
   ```

2. **Use only `.venv\Scripts\python.exe`.** A system Python 3.12 exists on
   this machine, so a bare `python tools\tmf8829_viewer.py` silently launches
   a second, competing instance.

3. **Reset the board without touching it** (also replays the boot banner;
   the device comes back `state=disabled`, so send `e` afterwards):

   ```powershell
   .\.venv\Scripts\python.exe -m pyocd reset -t stm32h563zitx
   ```

4. Measurements stopped but keys still answer (`#Err,timeout ... reg=0x8`,
   `state=measure`)? That's the known software-recoverable stall: `s` → `d` →
   `e`.

5. **The I3C bus wedged** — repeating
   `I3C-TX failed regAddr=0xF8 status=3 hi3c1.ErrorCode=0x20000`. The firmware
   handles this itself: after 3 consecutive failures it power-cycles the sensor
   and prints `I3C bus recovered`. **But it does not resume measuring**, because
   the power-cycle wipes the sensor's downloaded firmware — it comes back in the
   bootloader state (`Firmware Application Version 128.x`, serial `0x0`). Bring
   it back with:

   `s` → `d` → `e` → `m` → `v`

   Expect ~16-25 min of clean streaming before this happens; it is not yet
   understood. Nothing needs unplugging and no reflash is required.

6. **Streaming won't start / a script reports no frames?** The `v` toggle lives
   in MCU RAM and **survives sensor disable/enable**, so it may already be on —
   pressing `v` then turns it *off*. Read back the `Binary streaming ON/OFF`
   reply rather than counting presses.
