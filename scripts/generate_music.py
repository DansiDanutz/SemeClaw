"""Generate clean ambient background music for NERVIX meeting room."""
from __future__ import annotations

import math
import struct
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent.parent
AUDIO_DIR = ROOT / "assets" / "demo"
SAMPLE_RATE = 44100


def save_wav(data: np.ndarray, path: Path) -> None:
    """Save float64 mono/stereo array as 16-bit WAV."""
    data = np.clip(data, -1.0, 1.0)
    pcm = (data * 32767).astype(np.int16)
    if pcm.ndim == 1:
        pcm = np.column_stack((pcm, pcm))
    with open(path, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + pcm.nbytes))
        f.write(b"WAVEfmt ")
        f.write(struct.pack("<IHHIIHH", 16, 1, 2, SAMPLE_RATE, SAMPLE_RATE * 4, 4, 16))
        f.write(b"data")
        f.write(struct.pack("<I", pcm.nbytes))
        f.write(pcm.tobytes())


def adsr(samples: int, a: float, d: float, s: float, r: float) -> np.ndarray:
    """ADSR envelope: attack, decay, sustain level, release (all in seconds)."""
    env = np.zeros(samples)
    a_s = int(a * SAMPLE_RATE)
    d_s = int(d * SAMPLE_RATE)
    r_s = int(r * SAMPLE_RATE)
    sustain_s = max(0, samples - a_s - d_s - r_s)
    i = 0
    # attack
    if a_s > 0:
        env[i:i + a_s] = np.linspace(0, 1, a_s)
        i += a_s
    # decay
    if d_s > 0:
        env[i:i + d_s] = np.linspace(1, s, d_s)
        i += d_s
    # sustain
    if sustain_s > 0:
        env[i:i + sustain_s] = s
        i += sustain_s
    # release
    if r_s > 0 and i < samples:
        env[i:i + r_s] = np.linspace(s, 0, min(r_s, samples - i))
    return env


def sine_wave(freq: float, samples: int) -> np.ndarray:
    t = np.arange(samples) / SAMPLE_RATE
    return np.sin(2 * np.pi * freq * t)


def triangle_wave(freq: float, samples: int) -> np.ndarray:
    t = np.arange(samples) / SAMPLE_RATE
    return 2 * np.abs(2 * ((t * freq) % 1) - 1) - 1


def soft_square(freq: float, samples: int) -> np.ndarray:
    """Square wave with reduced harmonics for warmth."""
    t = np.arange(samples) / SAMPLE_RATE
    wave = np.zeros(samples)
    for n in range(1, 8, 2):
        wave += (1 / n) * np.sin(2 * np.pi * freq * n * t) * (0.7 ** n)
    return wave * 0.3


def lowpass(signal: np.ndarray, cutoff: float = 0.1) -> np.ndarray:
    """Simple one-pole lowpass filter."""
    out = np.zeros_like(signal)
    a = cutoff
    out[0] = signal[0] * a
    for i in range(1, len(signal)):
        out[i] = out[i - 1] * (1 - a) + signal[i] * a
    return out


def simple_reverb(signal: np.ndarray, decay: float = 0.4, delays: list[int] | None = None) -> np.ndarray:
    """Simple comb-filter reverb."""
    if delays is None:
        delays = [1102, 1321, 1745, 2137, 2611]
    out = signal.copy()
    for d in delays:
        if d >= len(signal):
            continue
        echo = np.zeros_like(signal)
        echo[d:] = signal[:-d] * decay
        out += echo
    return out / (1 + len(delays) * decay * 0.5)


def detuned_saw(freq: float, samples: int, detune: float = 0.003) -> np.ndarray:
    """Two slightly detuned saws for lush pad sound."""
    t = np.arange(samples) / SAMPLE_RATE
    s1 = ((t * freq) % 1) * 2 - 1
    s2 = ((t * freq * (1 + detune)) % 1) * 2 - 1
    return (s1 + s2) * 0.25


def generate_pad_chord(root_freq: float, chord_type: str, duration: float, volume: float = 0.15) -> np.ndarray:
    """Generate a lush pad chord."""
    samples = int(duration * SAMPLE_RATE)
    ratios = {
        "min7": [1, 1.1892, 1.4983, 1.7818],      # root, m3, 5, m7
        "maj7": [1, 1.2599, 1.4983, 1.8877],      # root, M3, 5, M7
        "maj":  [1, 1.2599, 1.4983],              # root, M3, 5
        "min":  [1, 1.1892, 1.4983],              # root, m3, 5
        "sus2": [1, 1.1225, 1.4983],              # root, 2, 5
        "add9": [1, 1.2599, 1.4983, 1.7818, 2.0], # root, M3, 5, m7, 9
    }
    freqs = [root_freq * r for r in ratios.get(chord_type, ratios["maj7"])]

    wave = np.zeros(samples)
    for f in freqs:
        wave += detuned_saw(f, samples) * volume
        # Add a sine an octave below for body
        wave += sine_wave(f * 0.5, samples) * volume * 0.6

    wave *= adsr(samples, 1.5, 1.0, 0.7, 2.5)
    wave = lowpass(wave, 0.08)
    return wave


def generate_bass_note(freq: float, duration: float, volume: float = 0.25) -> np.ndarray:
    """Deep soft bass."""
    samples = int(duration * SAMPLE_RATE)
    wave = soft_square(freq, samples) * 0.5
    wave += sine_wave(freq, samples) * 0.5
    wave *= adsr(samples, 0.05, 0.2, 0.6, 0.3)
    wave = lowpass(wave, 0.05)
    return wave * volume


def generate_arpeggio_note(freq: float, start: float, duration: float, volume: float = 0.08) -> np.ndarray:
    """Single plucky arpeggio note."""
    samples = int(duration * SAMPLE_RATE)
    wave = triangle_wave(freq, samples)
    wave *= adsr(samples, 0.01, 0.1, 0.0, 0.4)
    # Pan
    pad = int(start * SAMPLE_RATE)
    return wave * volume, pad


def note_freq(semitone: int) -> float:
    """A4 = 440Hz, semitone offset."""
    return 440.0 * (2 ** (semitone / 12))


def generate_track() -> np.ndarray:
    """Generate full 32-second ambient background track."""
    total_seconds = 32
    total_samples = total_seconds * SAMPLE_RATE
    master = np.zeros(total_samples)

    # Chord progression: Am7 - Fmaj7 - Cmaj7 - G6 (chill, professional)
    # Each chord 8 seconds
    chords = [
        (note_freq(-3), "min7", 0),      # A3 = Am7
        (note_freq(-8), "maj7", 8),      # F3 = Fmaj7
        (note_freq(-12), "maj7", 16),    # C3 = Cmaj7
        (note_freq(-5), "add9", 24),     # G3 = Gadd9 (G6-ish)
    ]

    # Pad chords
    for freq, ctype, start in chords:
        pad = generate_pad_chord(freq, ctype, 10, volume=0.18)
        s = int(start * SAMPLE_RATE)
        e = min(s + len(pad), total_samples)
        master[s:e] += pad[:e - s]

    # Bass line - root notes on beat 1 of each 2-bar phrase
    bass_notes = [
        (note_freq(-15), 0, 3.5),     # A2
        (note_freq(-20), 4, 3.5),     # F2
        (note_freq(-24), 8, 3.5),     # C2
        (note_freq(-17), 12, 3.5),    # G2
        (note_freq(-15), 16, 3.5),    # A2
        (note_freq(-20), 20, 3.5),    # F2
        (note_freq(-24), 24, 3.5),    # C2
        (note_freq(-17), 28, 3.5),    # G2
    ]
    for freq, start, dur in bass_notes:
        note = generate_bass_note(freq, dur)
        s = int(start * SAMPLE_RATE)
        e = min(s + len(note), total_samples)
        master[s:e] += note[:e - s]

    # Arpeggio: gentle 16th-note pattern on top
    # Am7 arpeggio: A3, C4, E4, G4
    arp_patterns = [
        # Am7: A3, C4, E4, G4
        ([-3, 0, 4, 7], 0, 8),
        # Fmaj7: F3, A3, C4, E4
        ([-8, -3, 0, 4], 8, 8),
        # Cmaj7: C3, E3, G3, B3
        ([-12, -8, -5, -2], 16, 8),
        # Gadd9: G3, B3, D4, A4
        ([-5, -2, 2, 9], 24, 8),
    ]

    for pattern, start_sec, dur_sec in arp_patterns:
        step = 0.25  # 16th notes at 60 BPM = 4 notes per second
        t = start_sec
        pattern_idx = 0
        while t < start_sec + dur_sec - 0.3:
            semitone = pattern[pattern_idx % len(pattern)]
            freq = note_freq(semitone)
            note, pad = generate_arpeggio_note(freq, t, 0.4, volume=0.06)
            s = pad
            e = min(s + len(note), total_samples)
            master[s:e] += note[:e - s]
            t += step
            pattern_idx += 1

    # Reverb on master (subtle)
    master = simple_reverb(master, decay=0.25)

    # Gentle sidechain-ish ducking every beat (subtle)
    beat_period = int(SAMPLE_RATE * 0.5)  # 120 BPM feel
    for i in range(0, total_samples, beat_period):
        end = min(i + beat_period, total_samples)
        beat_env = np.linspace(0.92, 1.0, end - i) ** 2
        master[i:end] *= beat_env

    # Normalize
    peak = np.max(np.abs(master))
    if peak > 0:
        master = master / peak * 0.85

    return master


def generate_short_jingle() -> np.ndarray:
    """Short 4-second brand jingle for ad transitions."""
    total_samples = 4 * SAMPLE_RATE
    master = np.zeros(total_samples)

    # N-E-R-V-I-X melodic motif: E4 - G#4 - B4 - C#5 - D#5 - F#5
    nervix_notes = [4, 8, 11, 13, 15, 18]  # E, G#, B, C#, D#, F#
    nervix_times = [0.0, 0.3, 0.6, 0.9, 1.2, 1.5]
    nervix_durs = [0.6, 0.6, 0.6, 0.6, 0.6, 1.5]

    for semitone, start, dur in zip(nervix_notes, nervix_times, nervix_durs):
        freq = note_freq(semitone)
        samples = int(dur * SAMPLE_RATE)
        # Soft bell-like tone
        wave = sine_wave(freq, samples) * 0.4
        wave += sine_wave(freq * 2, samples) * 0.15
        wave += triangle_wave(freq * 0.5, samples) * 0.1
        env = adsr(samples, 0.02, 0.3, 0.3, 1.0)
        wave *= env
        s = int(start * SAMPLE_RATE)
        e = min(s + len(wave), total_samples)
        master[s:e] += wave[:e - s]

    # Add a warm pad underneath
    pad = generate_pad_chord(note_freq(-12), "maj7", 4, volume=0.1)
    master[:len(pad)] += pad[:len(master)]

    master = simple_reverb(master, decay=0.35)
    peak = np.max(np.abs(master))
    if peak > 0:
        master = master / peak * 0.85
    return master


def main() -> None:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    print("Generating NERVIX background music...")
    track = generate_track()
    save_wav(track, AUDIO_DIR / "meeting_background.wav")
    print(f"  [OK] meeting_background.wav ({len(track)/SAMPLE_RATE:.1f}s)")

    print("Generating NERVIX brand jingle...")
    jingle = generate_short_jingle()
    save_wav(jingle, AUDIO_DIR / "nervix_jingle.wav")
    print(f"  [OK] nervix_jingle.wav ({len(jingle)/SAMPLE_RATE:.1f}s)")

    print("\nDone! Music files are in assets/demo/")
    print("  - meeting_background.wav  -> long ambient track for waiting/background")
    print("  - nervix_jingle.wav     -> short brand sound for ad transitions")


if __name__ == "__main__":
    main()
