"""THE FILE YOU EDIT. Save it while the engine runs — it hot-reloads with no
dropped notes and no audio restart. A syntax error just prints a traceback and
keeps the last working version playing, so experiment freely.

Contract — engine calls, once per voice per audio block:

    render_voice(v, frames, sr, ctrl) -> np.ndarray[float32] shape (frames,)

    v.freq   float   current frequency in Hz (pitch bend already applied)
    v.vel    float   note velocity 0..1
    v.gate   bool    True while the note is held (or sustain pedal down)
    v.state  dict    yours to keep between blocks: phase, envelope, filter...
    v.amp    float   SET THIS to the current envelope level; when it reaches ~0
                     after release the engine frees the voice
    ctrl     dict    live controllers, values 0..1:
                       ctrl.get(1)   mod wheel
                       ctrl.get(74)  filter cutoff knob (common default CC)
                       ctrl["bend"]  pitch wheel, -1..1 (already in v.freq)

Everything below is just one example patch. Rip it out and write your own.
"""

import numpy as np

# --- knobs: tweak and save -------------------------------------------------
DETUNE   = 0.006     # supersaw spread
ATTACK   = 0.005     # seconds
DECAY    = 0.20
SUSTAIN  = 0.7       # level 0..1
RELEASE  = 0.30
BASE_CUT = 800.0     # Hz, filter cutoff floor
ENV_CUT  = 6000.0    # Hz added by envelope
DRIVE    = 1.5
# ---------------------------------------------------------------------------


def _saw(phase):
    # naive saw in [-1,1) from a running phase in turns (0..1)
    return 2.0 * (phase - np.floor(phase + 0.5))


def render_voice(v, frames, sr, ctrl):
    st = v.state
    if not st:                         # first block for this voice: init
        st["phase"] = np.zeros(3)
        st["env"] = 0.0
        st["lp"] = 0.0                 # one-pole lowpass memory

    # three detuned saws -> supersaw
    inc = v.freq / sr
    ratios = (1.0 - DETUNE, 1.0, 1.0 + DETUNE)
    out = np.zeros(frames, dtype=np.float32)
    for i, r in enumerate(ratios):
        step = inc * r
        ph = st["phase"][i] + step * np.arange(frames)
        out += _saw(ph)
        st["phase"][i] = (st["phase"][i] + step * frames) % 1.0
    out *= 1.0 / len(ratios)

    # ADSR envelope, one step per block (cheap, click-free enough at 256 frames):
    # attack toward 1.0, decay to SUSTAIN while held, release to 0 when let go.
    env = st["env"]
    if v.gate:
        if not st.get("hit_peak"):
            env = _approach(env, 1.0, ATTACK, sr, frames)
            if env >= 0.999:
                st["hit_peak"] = True
        else:
            env = _approach(env, SUSTAIN, DECAY, sr, frames)
    else:
        env = _approach(env, 0.0, RELEASE, sr, frames)
        st["hit_peak"] = False
    st["env"] = env
    v.amp = env

    # one-pole lowpass, cutoff from envelope + mod/cutoff knob
    knob = ctrl.get(74, ctrl.get(1, 0.5))
    cutoff = BASE_CUT + ENV_CUT * env * (0.3 + 0.7 * knob)
    a = 1.0 - np.exp(-2.0 * np.pi * min(cutoff, sr * 0.45) / sr)
    lp = st["lp"]
    for n in range(frames):            # sample loop only for the filter recursion
        lp += a * (out[n] - lp)
        out[n] = lp
    st["lp"] = lp

    out *= env * v.vel
    return np.tanh(out * DRIVE).astype(np.float32)


def _approach(x, target, seconds, sr, frames):
    # move x toward target across one block at the given time-constant
    if seconds <= 0:
        return target
    coeff = np.exp(-frames / (seconds * sr))
    return target + (x - target) * coeff
