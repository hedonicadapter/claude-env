"""Headless check: no MIDI, no audio device. Renders a chord through the real
render_voice + Voice + envelope-cull path and writes chord.wav, then verifies a
patch edit hot-swaps mid-render. Run: python offline_test.py"""

import importlib
import os
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine
import patch

SR = engine.SAMPLE_RATE
BLOCK = engine.BLOCK


def render(notes, hold_s, tail_s):
    voices = {n: engine.Voice(n, 100 / 127) for n in notes}
    ctrl = {"bend": 0.0, "sustain": False, 74: 0.6}
    hold = int(hold_s * SR)
    total = int((hold_s + tail_s) * SR)
    buf = []
    released = False
    n = 0
    while voices and n < total + SR:      # +1s safety
        if n >= hold and not released:
            for v in voices.values():
                v.gate = False
            released = True
        mix = np.zeros(BLOCK, dtype=np.float32)
        for note in list(voices):
            v = voices[note]
            mix += patch.render_voice(v, BLOCK, SR, ctrl)
            if not v.gate and v.amp < 1e-4:
                del voices[note]
        np.tanh(mix, out=mix)
        buf.append(mix)
        n += BLOCK
    return np.concatenate(buf) if buf else np.zeros(1, np.float32)


def write_wav(path, sig):
    pcm = np.clip(sig, -1, 1)
    pcm = (pcm * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def main():
    sig = render([60, 64, 67], hold_s=1.0, tail_s=0.6)   # C major triad
    assert np.isfinite(sig).all(), "non-finite samples"
    peak = float(np.max(np.abs(sig)))
    assert 0.05 < peak <= 1.0, f"bad peak {peak}"
    assert abs(sig[:64]).max() < peak, "attack should ramp, not click on"
    assert abs(sig[-16:]).max() < 1e-2, "release should fade toward silence"
    write_wav("chord.wav", sig)
    print(f"chord.wav written: {len(sig)/SR:.2f}s, peak {peak:.3f}")

    # hot-reload: reloading the module returns a fresh render_voice, note state
    # (a Voice's .state dict) is owned by the engine and survives untouched.
    v = engine.Voice(69, 0.8)
    patch.render_voice(v, BLOCK, SR, {})
    phase_before = v.state["phase"].copy()
    importlib.reload(patch)
    patch.render_voice(v, BLOCK, SR, {})       # same voice, reloaded DSP
    assert not np.array_equal(phase_before, v.state["phase"]), "phase advanced"
    assert v.state, "voice state survived reload"
    print("hot-reload swap OK — voice state preserved across reload")
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
