"""Persistent synth engine: MIDI in, audio out, live patch reload.

Nothing here should need editing while jamming. Edit patch.py instead — it
reloads on save without dropping held notes or restarting audio.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import queue
import sys
import threading
import time
import traceback

import numpy as np
# sounddevice + mido imported lazily inside run()/list_devices() so the DSP
# core (Voice, render, reload) can be imported and tested without audio hardware.

SAMPLE_RATE = 48_000
BLOCK = 256          # frames per audio callback; ~5.3ms latency at 48k
CHANNELS = 2
BEND_RANGE = 2.0     # pitch-bend range in semitones (±)


class Voice:
    """One sounding note. `state` is a scratch dict owned by the patch —
    engine never touches its contents, so patch reloads keep phase/env/filter
    continuity. Engine owns lifecycle (gate, freq, culling) only."""

    __slots__ = ("note", "base_freq", "freq", "vel", "gate", "pending", "state", "amp")

    def __init__(self, note: int, vel: float):
        self.note = note
        self.base_freq = 440.0 * 2 ** ((note - 69) / 12)
        self.freq = self.base_freq
        self.vel = vel
        self.gate = True         # effective note-on (pedal can keep it True after key up)
        self.pending = False     # key released but held by sustain pedal
        self.state: dict = {}
        self.amp = 0.0           # last envelope level; engine culls when released + silent


class PatchHolder:
    """Holds the current patch module, loaded from an explicit file path (not
    sys.path) so a read-only engine — e.g. one built into the nix store — can
    watch and reload a patch.py that lives in the user's writable checkout.
    Watcher swaps `.mod` atomically (GIL); a broken edit keeps the last good
    module so audio never dies."""

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.mod = self._load()
        self.mtime = self._mtime()

    def _load(self):
        spec = importlib.util.spec_from_file_location("patch", self.path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.render_voice  # fail fast if contract missing
        return mod

    def _mtime(self) -> float:
        try:
            return os.path.getmtime(self.path)
        except OSError:
            return 0.0

    def maybe_reload(self):
        m = self._mtime()
        if m == self.mtime:
            return
        self.mtime = m
        try:
            self.mod = self._load()
            print(f"[reload] {self.path} loaded {time.strftime('%H:%M:%S')}")
        except Exception:
            print("[reload] FAILED — keeping previous patch:")
            traceback.print_exc()


def watcher(holder: PatchHolder, stop: threading.Event):
    while not stop.is_set():
        holder.maybe_reload()
        time.sleep(0.15)


def make_callback(holder: PatchHolder, events: queue.Queue, ctrl: dict):
    voices: dict[int, Voice] = {}

    def apply_bend():
        semis = ctrl["bend"] * BEND_RANGE
        mul = 2 ** (semis / 12)
        for v in voices.values():
            v.freq = v.base_freq * mul

    def drain():
        while True:
            try:
                msg = events.get_nowait()
            except queue.Empty:
                return
            t = msg.type
            if t == "note_on" and msg.velocity > 0:
                voices[msg.note] = Voice(msg.note, msg.velocity / 127)
            elif t == "note_off" or (t == "note_on" and msg.velocity == 0):
                v = voices.get(msg.note)
                if v:
                    if ctrl.get("sustain"):
                        v.pending = True     # keep sounding until pedal up
                    else:
                        v.gate = False
            elif t == "control_change":
                ctrl[msg.control] = msg.value / 127
                if msg.control == 64:        # sustain pedal
                    down = msg.value >= 64
                    ctrl["sustain"] = down
                    if not down:             # pedal up: release everything it held
                        for v in voices.values():
                            if v.pending:
                                v.gate = False
                                v.pending = False
            elif t == "pitchwheel":
                ctrl["bend"] = msg.pitch / 8192  # -1..1
                apply_bend()

    def callback(outdata, frames, time_info, status):
        if status:
            print(status, file=sys.stderr)
        drain()
        mod = holder.mod
        mix = np.zeros(frames, dtype=np.float32)
        dead = []
        for note, v in voices.items():
            try:
                mix += mod.render_voice(v, frames, SAMPLE_RATE, ctrl)
            except Exception:
                traceback.print_exc()
                v.amp = 0.0
            if not v.gate and v.amp < 1e-4:   # released and faded out
                dead.append(note)
        for note in dead:
            voices.pop(note, None)
        np.tanh(mix, out=mix)                 # soft clip
        outdata[:] = np.repeat(mix[:, None], CHANNELS, axis=1)

    return callback


def list_devices():
    import sounddevice as sd
    import mido
    print(sd.query_devices())
    print("\nMIDI inputs:")
    for n in mido.get_input_names():
        print(" ", n)


def resolve_patch(explicit: str | None) -> str:
    """Find patch.py to load+watch. Priority: --patch, $SYNTH_PATCH, ./patch.py,
    ./synth/patch.py, then the copy next to this engine. Prefers the working
    tree so a nix-store engine reloads edits you can actually make."""
    for cand in (explicit, os.environ.get("SYNTH_PATCH"),
                 "patch.py", os.path.join("synth", "patch.py"),
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), "patch.py")):
        if cand and os.path.isfile(cand):
            return cand
    sys.exit("No patch.py found. Pass --patch PATH or run from the repo.")


def run(midi_name: str | None, out_device, patch_path: str):
    import sounddevice as sd
    import mido
    stop = threading.Event()
    holder = PatchHolder(patch_path)
    events: queue.Queue = queue.Queue()
    ctrl = {"bend": 0.0, "sustain": False}

    names = mido.get_input_names()
    if not names:
        sys.exit("No MIDI inputs found. Plug in a keyboard, or run --list.")
    if midi_name:
        port = next((n for n in names if midi_name.lower() in n.lower()), None)
        if port is None:
            sys.exit(f"No MIDI input matches {midi_name!r}. Available:\n  "
                     + "\n  ".join(names))
    else:
        # auto-pick: skip ALSA "Midi Through" and virtual RtMidi loopbacks —
        # they carry no keyboard data. Fall back to first if only those exist.
        real = [n for n in names if "through" not in n.lower()
                and "rtmidi" not in n.lower()]
        port = (real or names)[0]

    threading.Thread(target=watcher, args=(holder, stop), daemon=True).start()
    cb = make_callback(holder, events, ctrl)

    def on_midi(msg):
        if msg.type in ("note_on", "note_off", "control_change", "pitchwheel"):
            events.put(msg)

    print(f"MIDI in : {port}")
    print("Audio   :", sd.query_devices(out_device, "output")["name"]
          if out_device is not None else "default")
    print(f"Editing : {holder.path}  (save to hot-reload)\nCtrl-C to stop.\n")

    with mido.open_input(port, callback=on_midi), sd.OutputStream(
        samplerate=SAMPLE_RATE, blocksize=BLOCK, channels=CHANNELS,
        dtype="float32", device=out_device, callback=cb,
    ):
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()


def main():
    ap = argparse.ArgumentParser(description="Hot-reloadable MIDI synth.")
    ap.add_argument("--list", action="store_true", help="list audio + MIDI devices")
    ap.add_argument("--midi", help="substring of MIDI input port name")
    ap.add_argument("--device", help="audio output device (name or index)")
    ap.add_argument("--patch", help="path to the patch.py to load and hot-reload")
    args = ap.parse_args()
    if args.list:
        list_devices()
        return
    dev = args.device
    if dev is not None and dev.isdigit():
        dev = int(dev)
    run(args.midi, dev, resolve_patch(args.patch))


if __name__ == "__main__":
    main()
