"""
Light-hearted background music for the pet reels.

The channel is animals-only with no voiceover, so the audio track is the whole
soundtrack. Every major pet account on Instagram/TikTok runs upbeat instrumental
stock music over silent animal footage - the music does the emotional work a
voiceover used to do here.

Why this is synthesised rather than streamed from a stock-music API:

  * No stock-music provider is reachable from this project, and the usual ones
    want an API key and a per-request fee, which this channel does not have.
  * A third-party track fetched at run time is a licensing question nobody in
    this repo can answer later, and a silent video is a dead video. Music we
    generate ourselves is unambiguously ours to publish.
  * It is deterministic. Same seed, same track, every run - so a reel can be
    re-cut later and still sound the same.

Structure per mood: a plucked major-key arpeggio over a soft pad, with a light
kick and hat. Deliberately simple - it has to sit *under* the footage without
pulling attention to itself, which is the whole craft of reel background music.
"""

import os
import struct
import wave

SAMPLE_RATE = 44100

# Moods are chosen per beat from what the pet is actually doing, so the music
# tracks the action instead of being one flat bed for the whole reel.
MOODS = {
    # name          bpm  root  brightness  swing  hat
    "playful":  {"bpm": 146, "root": 62, "bright": 0.85, "swing": 0.55, "hat": 0.30},
    "curious":  {"bpm": 112, "root": 60, "bright": 0.65, "swing": 0.30, "hat": 0.18},
    "calm":     {"bpm": 88,  "root": 57, "bright": 0.45, "swing": 0.15, "hat": 0.10},
    "sleepy":   {"bpm": 64,  "root": 55, "bright": 0.30, "swing": 0.05, "hat": 0.05},
}

# I - V - vi - IV in a major key: the four-chord loop behind most upbeat
# background music, and unremarkable in the good way.
_PROGRESSION = [(0, 4), (7, 4), (9, 4), (5, 4)]

# One octave of a major scale, semitone offsets from the root.
_MAJOR = [0, 2, 4, 5, 7, 9, 11, 12, 14, 16]


def _note_hz(root_hz: float, semitones: int) -> float:
    return root_hz * (2.0 ** (semitones / 12.0))


def _pluck(buf, start: int, length: int, hz: float, amp: float, bright: float):
    """One plucked-string voice: sine fundamental plus decaying harmonics.

    A bare sine sounds like a test tone. Rolling off the upper harmonics over
    the note's life is what turns it into something that reads as a cheap,
    cheerful ukulele or music-box, which is the register these reels want.
    """
    n = min(length, len(buf) - start)
    if n <= 0 or amp <= 0.0 or hz <= 0.0:
        return
    t = [i / SAMPLE_RATE for i in range(n)]
    for k, weight in ((1, 1.0), (2, 0.45 * bright), (3, 0.22 * bright), (4, 0.10 * bright)):
        decay = 2.2 + k * 0.8
        for i in range(n):
            env = pow(1.0 - i / n, 1.4) * pow(2.718281828, -decay * t[i] * 3.0)
            buf[start + i] += weight * env * amp * 0.25 * _sine(hz * k, t[i])


def _sine(hz: float, t: float) -> float:
    return _sin(hz * t * 6.283185307)


def _sin(x: float) -> float:
    """Cheap sine. math.sin is importable and fast enough at these lengths."""
    import math

    return math.sin(x)


def _kick(buf, start: int, amp: float):
    """A short low thump. Two sines, the second an octave down, decaying fast."""
    n = min(int(SAMPLE_RATE * 0.16), len(buf) - start)
    if n <= 0:
        return
    for i in range(n):
        t = i / SAMPLE_RATE
        f = 110.0 * pow(2.0, -3.0 * t / 0.16)
        buf[start + i] += amp * 0.5 * pow(1.0 - i / n, 2.2) * _sine(f, t)


def _hat(buf, start: int, amp: float):
    """A short noise tick. Seeded by the caller so runs stay reproducible."""
    n = min(int(SAMPLE_RATE * 0.05), len(buf) - start)
    if n <= 0:
        return
    rng = _lcg(start)
    for i in range(n):
        buf[start + i] += amp * 0.22 * pow(1.0 - i / n, 5.0) * (rng() * 2.0 - 1.0)


def _lcg(seed: int):
    """A tiny linear congruential generator.

    `random` would do, but the module-level random stream is shared with the
    image and video seed selection elsewhere in the agency, and music should
    not shift because an image provider was retried.
    """
    state = (seed * 2654435761 + 1013904223) & 0xFFFFFFFF

    def nxt() -> float:
        nonlocal state
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        return state / 0x7FFFFFFF

    return nxt


def build_track(mood: str, duration_sec: float, seed: int) -> list:
    """Render one stereo-safe mono float track in roughly -1.0..1.0."""
    cfg = MOODS.get(mood) or MOODS["curious"]
    root_hz = _note_hz(261.63, cfg["root"] - 60)  # root_hz is a mid-register C
    total = int(SAMPLE_RATE * max(1.0, duration_sec))
    buf = [0.0] * total

    beat = 60.0 / cfg["bpm"]
    eighth = beat / 2.0
    step = 0
    pos = 0.0

    while int(pos * SAMPLE_RATE) < total:
        bar = int(pos // (beat * 4)) % len(_PROGRESSION)
        degree, _ = _PROGRESSION[bar]
        in_bar = pos % (beat * 4)

        # Chord pad: one soft sustained tone per chord tone.
        if abs(in_bar) < 1e-6:
            for chord_tone in (0, 4, 7):
                start = int(pos * SAMPLE_RATE)
                n = min(int(beat * 4 * SAMPLE_RATE), total - start)
                base = _note_hz(root_hz, degree + chord_tone)
                for i in range(0, n, 8):
                    t = i / SAMPLE_RATE
                    env = min(1.0, t * 2.0) * min(1.0, (n - i) / (SAMPLE_RATE * 0.8))
                    v = 0.10 * env * (_sine(base, t) + 0.5 * _sine(base * 2, t)) / 1.5
                    buf[start + i] += v

        # Arpeggio: walk the chord's tones, nudged by the mood's brightness so
        # playful runs climb and calm ones stay put.
        arp = _MAJOR[(degree + step * (2 if cfg["bright"] > 0.6 else 1)) % len(_MAJOR)]
        _pluck(buf, int(pos * SAMPLE_RATE), int(eighth * SAMPLE_RATE * 1.6),
               _note_hz(root_hz, arp), 0.55, cfg["bright"])

        # Downbeat kick, offbeat hat.
        if abs(in_bar) < 1e-6 or abs(in_bar - beat * 2) < 1e-6:
            _kick(buf, int(pos * SAMPLE_RATE), 0.55)
        if cfg["hat"] > 0.01 and int(pos / eighth) % 2 == 1:
            _hat(buf, int(pos * SAMPLE_RATE), cfg["hat"])

        step += 1
        swing = eighth * (cfg["swing"] if step % 2 else (1.0 - cfg["swing"]))
        pos += beat if step % 8 == 0 else swing

    peak = max((abs(v) for v in buf), default=0.0)
    if peak > 0:
        gain = 0.89 / peak
        buf = [v * gain for v in buf]
    return buf


def write_music_wav(mood: str, duration_sec: float, seed: int, out_path: str) -> str:
    """Render and write a 16-bit mono WAV the ffmpeg mux can consume."""
    samples = build_track(mood, duration_sec, seed)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with wave.open(out_path, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(SAMPLE_RATE)
        frames = bytearray()
        for v in samples:
            frames += struct.pack("<h", int(max(-1.0, min(1.0, v)) * 32767))
        fh.writeframes(bytes(frames))
    return out_path


def mood_for_action(action: str) -> str:
    """Pick a mood from what the pet is doing, so music tracks the action."""
    text = (action or "").lower()
    if any(w in text for w in ("sleep", "nap", "loaf", "doze", "rest", "curl", "nest", "flop")):
        return "sleepy"
    if any(w in text for w in ("zoom", "bounc", "dart", "sprint", "chase", "rear", "cavort")):
        return "playful"
    if any(w in text for w in ("chew", "gnaw", "nibble", "munch", "burrow", "dig", "invest")):
        return "playful"
    if any(w in text for w in ("look up", "curious", "sniff", "watch", "gaze", "pause", "sit")):
        return "curious"
    return "calm"
