# Hot-reloadable MIDI synth

A live-coding software synth. A persistent engine holds your MIDI connection
and the audio stream open; the sound-making DSP lives in `patch.py`, which
reloads the instant you save it — **no dropped notes, no audio restart**. Save a
broken edit and it prints the error and keeps the last working patch playing,
so you can tweak fearlessly while holding a chord.

```
engine.py       persistent host: MIDI in, audio out, file watcher, voices
patch.py        the file you edit — oscillators, envelope, filter (hot-reloaded)
offline_test.py headless render to chord.wav (no hardware needed)
run.sh          venv setup + launch
```

## Run with Nix (recommended)

`flake.nix` provides everything — Python, NumPy, PortAudio, and the MIDI stack
— with no venv and no system packages to install.

```bash
cd synth

nix run .                 # launch the synth (grabs first MIDI in + default audio)
nix run . -- --list       # list audio output + MIDI input devices
nix run . -- --midi MPK   # pick a MIDI port by name substring
nix run . -- --device 3   # pick an audio output by index or name

nix develop               # drop into a shell with all deps, then:
python engine.py          #   run against the writable ./patch.py
python offline_test.py    #   headless render, no hardware
```

Run `nix run .` **from inside `synth/`** (or `nix run ./synth` from the repo
root): the engine loads `./patch.py` from your checkout, so saving it
hot-reloads. Run from anywhere else and it falls back to the read-only copy in
the nix store (plays, but no live reload) — or point it explicitly with
`nix run . -- --patch /path/to/patch.py`.

## Run with pip (no Nix)

```bash
cd synth
./run.sh              # first run builds .venv and installs deps
./run.sh --list       # list audio output + MIDI input devices
./run.sh --midi "MPK" # pick a MIDI port by name substring
./run.sh --device 3   # pick an audio output by index or name
```

By default it grabs the first MIDI input and the system default audio output.
Play the keyboard, then open `patch.py` in your editor and change a number —
say `DETUNE`, `RELEASE`, or `BASE_CUT` — and save. The sound changes underneath
your fingers.

## Requirements

- Python 3.9+
- A MIDI keyboard (USB class-compliant works with no drivers)
- Working audio out
- Linux also needs a PortAudio backend and ALSA/JACK; on Debian/Ubuntu:
  `sudo apt install libportaudio2 librtmidi-dev`
- macOS and Windows: `run.sh` installs everything via pip, no system packages

`run.sh` installs `numpy`, `sounddevice` (PortAudio), `mido` + `python-rtmidi`
(MIDI) into `synth/.venv`.

## Editing patches

`patch.py` exports one function the engine calls per voice, per audio block:

```python
render_voice(v, frames, sr, ctrl) -> np.ndarray[float32]  # shape (frames,)
```

| field      | meaning                                                              |
|------------|----------------------------------------------------------------------|
| `v.freq`   | frequency in Hz, pitch bend already applied                          |
| `v.vel`    | velocity, 0..1                                                        |
| `v.gate`   | `True` while held (sustain pedal counts as held)                     |
| `v.state`  | your scratch dict — keep phase/envelope/filter here across blocks    |
| `v.amp`    | **set this** to the current envelope level; the engine frees a voice when it hits ~0 after release |
| `ctrl`     | live controllers 0..1: `ctrl.get(1)` mod wheel, `ctrl.get(74)` cutoff knob, `ctrl["bend"]` pitch wheel -1..1 |

Because `v.state` is owned by the engine and never reset on reload, a saved
edit keeps every held voice's phase and envelope continuous — you hear the new
timbre without a click. The included patch is a 3-saw supersaw → ADSR →
one-pole lowpass; replace it with whatever you want.

## Play in Logic Pro while Claude tweaks the sound live (macOS)

This is the "you play, Claude changes the sound on request" setup. The synth
runs as a separate app, but its audio is piped into a Logic track (so you get
recording and Logic's effects), and Claude's edits reach your Mac over git and
hot-reload while you hold a chord.

### One-time setup

**A. Route MIDI into the synth (IAC).** IAC is macOS's built-in "virtual MIDI
cable". Open *Audio MIDI Setup* → *Window ▸ Show MIDI Studio* → double-click
*IAC Driver* → check **Device is online**.

**B. Route the synth's audio back into Logic (BlackHole).** BlackHole is a free
virtual audio cable. Install it:

```bash
brew install blackhole-2ch
```

**C. Let Logic use your speakers and BlackHole at once (Aggregate Device).** In
*Audio MIDI Setup* click **+ ▸ Create Aggregate Device**, then tick both your
normal output (e.g. *MacBook Speakers* or your interface) **and** *BlackHole
2ch*. In Logic: *Settings ▸ Audio* → set both **Output** and **Input** to this
aggregate device.

**D. Add the synth as a track in Logic.** New *Software Instrument* track →
click the instrument slot → *AU Instruments ▸ (Apple) ▸ External Instrument*.
In it set:
- **MIDI Destination** = *IAC Bus 1*
- **Input** = the *BlackHole* channels

### Each session

1. Start the synth, listening to IAC and playing out through BlackHole:
   ```bash
   cd synth
   nix run . -- --midi IAC --device BlackHole
   ```
2. In another terminal, start the live sync so Claude's pushes reach you:
   ```bash
   ./synth/live-sync.sh
   ```
3. Arm/monitor the Logic track and play your keyboard. Sound flows:
   **your keys → Logic → IAC → synth → BlackHole → Logic track → speakers.**
4. Tell Claude what to change ("brighter", "longer release", "detune the
   saws"). Claude edits `patch.py` and pushes; `live-sync.sh` pulls it within
   ~2s; the synth reloads with no dropped notes.

### Good to know

- Tweaks take a couple of seconds (a git round-trip) — great for "make it
  X", not for per-note automation.
- It is **not** a real plugin and **not** locked to Logic's clock; it's a
  separate audio engine bridged in. For most "jam and shape the tone" use you
  won't notice.
- If playback glitches, raise Logic's *Settings ▸ Audio ▸ I/O Buffer Size*.
- Only Claude should edit `patch.py` while `live-sync.sh` runs; your own edits
  to that file get overwritten on the next pull.

## How hot reload works

- **Engine (`engine.py`)** never needs restarting. It parses MIDI on one thread
  into a queue, owns voice allocation, and calls `patch.render_voice` from the
  audio callback. A watcher thread polls `patch.py`'s mtime and, on change,
  `importlib.reload`s it and atomically swaps the module reference. A failed
  reload is caught — the previous module stays live.
- **Voice state lives in the engine**, not the patch, so reloading pure DSP code
  can't drop or restart held notes.

## Test without hardware

```bash
cd synth && python offline_test.py    # writes chord.wav, checks the reload path
```

Renders a C-major triad through the real voice/envelope/reload code and asserts
the attack ramps, the release fades, and voice state survives a reload.
