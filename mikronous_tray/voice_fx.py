"""Machine-spirit voice: post-processing that turns an ordinary Piper voice into a servitor / vox-caster sound.

Pure numpy, fully local, no audio assets: every effect is synthesised (ring modulator, comb resonator,
band-limit, bit-crush, soft clip, hiss, static clicks). Original sound design; nothing is sampled from any game.

Pitch: Piper is asked to speak faster (``length_scale = factor``) and the result is resampled by ``factor``,
which lowers the pitch and restores normal speed without a phase vocoder.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Preset:
    name: str
    label: str
    pitch: float = 1.0           # < 1 lowers the voice (and length_scale for Piper is set to the same value)
    noise_scale: float = 0.667   # Piper prosody variation; lower = flatter, more mechanical
    ring_hz: float = 0.0
    ring_mix: float = 0.0
    comb_ms: float = 0.0
    comb_fb: float = 0.0
    band: tuple[float, float] | None = None
    crush_bits: int = 0
    crush_hold: int = 1
    drive: float = 0.0
    hiss: float = 0.0
    crackle: float = 0.0
    clicks: bool = False


PRESETS: dict[str, Preset] = {
    "servitor": Preset("servitor", "servitor — low, metallic, measured", pitch=0.82, noise_scale=0.4, ring_hz=45, ring_mix=0.35,
                       comb_ms=6.0, comb_fb=0.45, band=(180, 4200), crush_bits=10, clicks=True),
    "vox-caster": Preset("vox-caster", "vox-caster — narrow radio with static", pitch=0.92, noise_scale=0.5, band=(450, 3000),
                         drive=2.5, hiss=0.012, crackle=0.004, clicks=True),
    "cogitator": Preset("cogitator", "cogitator — crushed, buzzing logic engine", pitch=0.9, noise_scale=0.33, ring_hz=90,
                        ring_mix=0.3, comb_ms=3.0, comb_fb=0.6, crush_bits=6, crush_hold=3, band=(150, 5000)),
    "none": Preset("none", "none — plain Piper voice"),
}
ORDER = ("servitor", "vox-caster", "cogitator", "none")


def preset(name: str) -> Preset:
    return PRESETS.get((name or "").lower(), PRESETS["servitor"])


# ----------------------------------------------------------------------------- building blocks (float32 in -1..1)
def to_float(x: np.ndarray) -> np.ndarray:
    return x.astype(np.float32) / 32768.0


def to_int16(x: np.ndarray) -> np.ndarray:
    return (np.clip(x, -1.0, 1.0) * 32767.0).astype(np.int16)


def resample(x: np.ndarray, factor: float) -> np.ndarray:
    """Stretch by ``1/factor`` samples (factor < 1 → longer, played at the same rate → lower pitch)."""
    if factor == 1.0 or len(x) < 2:
        return x
    n = max(1, int(round(len(x) / factor)))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def ring_mod(x: np.ndarray, rate: int, hz: float, mix: float) -> np.ndarray:
    if hz <= 0 or mix <= 0:
        return x
    carrier = np.sin(2 * np.pi * hz * np.arange(len(x)) / rate).astype(np.float32)
    return (1 - mix) * x + mix * x * carrier


def comb(x: np.ndarray, rate: int, delay_ms: float, feedback: float) -> np.ndarray:
    """Feedback comb filter: the hollow, metallic resonance of a voice through a grille."""
    d = int(rate * delay_ms / 1000)
    if d <= 0 or feedback <= 0:
        return x
    y = x.copy()
    for start in range(d, len(y), d):                  # block-wise recurrence: y[n] += fb * y[n-d]
        end = min(start + d, len(y))
        y[start:end] += feedback * y[start - d:end - d]
    return y / (1 + feedback)


def bandpass(x: np.ndarray, rate: int, lo: float, hi: float) -> np.ndarray:
    if len(x) < 8:
        return x
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), 1 / rate)
    mask = ((freqs >= lo) & (freqs <= hi)).astype(np.float32)
    edge = 60.0                                        # soft edges avoid ringing
    mask = np.maximum(mask, np.clip(1 - (lo - freqs) / edge, 0, 1) * (freqs < lo))
    mask = np.maximum(mask, np.clip(1 - (freqs - hi) / edge, 0, 1) * (freqs > hi))
    return np.fft.irfft(spec * mask, n=len(x)).astype(np.float32)


def bitcrush(x: np.ndarray, bits: int, hold: int = 1) -> np.ndarray:
    if bits <= 0 and hold <= 1:
        return x
    y = x
    if hold > 1:
        y = np.repeat(y[::hold], hold)[: len(x)]
    if bits > 0:
        q = float(2 ** (bits - 1))
        y = np.round(y * q) / q
    return y.astype(np.float32)


def drive(x: np.ndarray, amount: float) -> np.ndarray:
    if amount <= 0:
        return x
    return (np.tanh(x * amount) / np.tanh(amount)).astype(np.float32)


def noise_bed(n: int, hiss: float, crackle: float, rng: np.random.Generator) -> np.ndarray:
    out = np.zeros(n, dtype=np.float32)
    if hiss > 0:
        out += hiss * rng.standard_normal(n).astype(np.float32)
    if crackle > 0:
        pops = rng.random(n) < crackle / 10
        out += np.where(pops, rng.uniform(-0.5, 0.5, n), 0).astype(np.float32)
    return out


def vox_click(rate: int, rng: np.random.Generator, ms: float = 70) -> np.ndarray:
    """A short burst of band-limited static: the channel opening or closing."""
    n = int(rate * ms / 1000)
    burst = rng.standard_normal(n).astype(np.float32) * np.linspace(0.35, 0.0, n, dtype=np.float32)
    return bandpass(burst, rate, 800, 5000)


def normalize(x: np.ndarray, peak: float = 0.89) -> np.ndarray:
    m = float(np.max(np.abs(x))) if len(x) else 0.0
    return x if m < 1e-6 else (x * (peak / m)).astype(np.float32)


# ----------------------------------------------------------------------------- the chain
def apply(samples: np.ndarray, rate: int, name: str, seed: int = 7) -> np.ndarray:
    """int16 mono in, int16 mono out. ``none`` returns the input unchanged."""
    p = preset(name)
    if p.name == "none":
        return samples
    rng = np.random.default_rng(seed)
    x = to_float(samples)
    x = resample(x, p.pitch)
    x = ring_mod(x, rate, p.ring_hz, p.ring_mix)
    x = comb(x, rate, p.comb_ms, p.comb_fb)
    if p.band:
        x = bandpass(x, rate, *p.band)
    x = drive(x, p.drive)
    x = bitcrush(x, p.crush_bits, p.crush_hold)
    x = normalize(x)
    x = x + noise_bed(len(x), p.hiss, p.crackle, rng)
    if p.clicks:
        gap = np.zeros(int(rate * 0.04), dtype=np.float32)
        x = np.concatenate([vox_click(rate, rng), gap, x, gap, vox_click(rate, rng)])
    return to_int16(normalize(x))
