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
| `b` | toggle binary streaming *(shim)* |
| `f` | dump last frame at full resolution *(shim)* |
| `t` | toggle per-sub-frame timing report *(shim)* — prints `#T,n=..,bus=..,emit=..,period=..` every 30 sub-frames, in µs |
| `+` / `-` | log level up / down — **leave at default** |
| `a` `h` `p` `u` `w` `x` `z` `#` | registers · help · power down · get config · wake · clock corr · histogram · reset |

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

**Startup sequence:** `e` → `s` → `c` until the target Preconfig → `m` → `b`

---

## Commands

Flash — `pio` is not on PATH; use the PlatformIO Core CLI terminal.

Upload goes **over SWD via pyocd** (`upload_protocol = custom`), not the
ST-Link's virtual disk — Defender blocks that disk from mounting on this
machine (CLAUDE.md, 08-17). No `--upload-port` is needed, and flashing works
even while something holds COM5. `programmed 0 bytes ... identical` on an
unchanged build is a verify-skip, not a failure.

```powershell
pio run --target upload
```

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

Record 20 seconds to `.captures/`.

```powershell
.\.venv\Scripts\python.exe tools\tmf8829_subframe.py capture --seconds 20
```

Re-verify the sub-frame interleave (32×32 or 48×32 only).

```powershell
.\.venv\Scripts\python.exe tools\tmf8829_subframe.py solve
```

### Viewer options

`--port COM5` · `--baud 2000000` · `--dist-min 100` · `--dist-max 3000` ·
`--conf-min 40` · `--smooth 5` (temporal median, `1` = off) ·
`--colormap turbo|jet|inferno|viridis|magma|plasma` · `--scale 24` ·
`--raw-order` · `--no-gui`

In the window: `q`/Esc quits, `M` toggles the gray mask, `e c m s b f` forward
to the board.

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
