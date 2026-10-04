"""Machine-spirit effect chain: shapes, levels, filters, determinism (no audio device needed)."""
import pytest

np = pytest.importorskip("numpy")
from mikronous_tray import voice_fx as fx  # noqa: E402

RATE = 22050


def tone(hz, seconds=1.0, amp=0.5):
    t = np.arange(int(RATE * seconds)) / RATE
    return (amp * 32767 * np.sin(2 * np.pi * hz * t)).astype(np.int16)


def test_none_is_identity_and_presets_exist():
    x = tone(220)
    assert fx.apply(x, RATE, "none") is x
    assert set(fx.ORDER) == set(fx.PRESETS) and fx.preset("bogus").name == "servitor"


@pytest.mark.parametrize("name", ["servitor", "vox-caster", "cogitator"])
def test_presets_produce_bounded_int16_of_expected_length(name):
    x = tone(220, 1.0)
    y = fx.apply(x, RATE, name)
    p = fx.PRESETS[name]
    expected = len(x) / p.pitch + (2 * int(RATE * 0.07) + 2 * int(RATE * 0.04) if p.clicks else 0)
    assert y.dtype == np.int16 and abs(len(y) - expected) <= 0.01 * expected
    assert np.max(np.abs(y.astype(np.int32))) <= 32767 and np.max(np.abs(y)) > 20000
    assert np.array_equal(y, fx.apply(x, RATE, name))                # deterministic with the fixed seed


def test_building_blocks():
    low = fx.to_float(tone(100))
    passed = fx.bandpass(low, RATE, 450, 3000)
    assert 20 * np.log10(np.std(passed) / np.std(low)) < -20          # 100 Hz is cut by more than 20 dB
    mid = fx.to_float(tone(1000))
    assert np.std(fx.bandpass(mid, RATE, 450, 3000)) > 0.8 * np.std(mid)
    assert len(fx.resample(mid, 0.8)) == round(len(mid) / 0.8)
    crushed = fx.bitcrush(mid, 4, 3)
    assert len(np.unique(np.round(crushed, 6))) <= 2 ** 4 + 1
    assert fx.comb(mid, RATE, 0, 0.5) is mid and fx.ring_mod(mid, RATE, 0, 0) is mid
    boosted = fx.low_shelf(fx.to_float(tone(100)), RATE, 6.0)
    assert 20 * np.log10(np.std(boosted) / np.std(fx.to_float(tone(100)))) > 4.5
    assert abs(20 * np.log10(np.std(fx.low_shelf(mid, RATE, 6.0)) / np.std(mid))) < 1.0
    d = fx.drive(mid, 3.0)
    assert np.max(np.abs(d)) <= 1.0 + 1e-6


def test_depth_and_metal_dials():
    base = fx.preset("servitor")
    assert fx.preset("servitor", 50, 50) is base and len(base.combs) == 2
    assert (base.pitch, base.ring_mix, base.combs, base.low_shelf_db) == (0.655, 0.65, ((6.0, 0.68), (9.7, 0.50)), 6.8)
    deep = fx.preset("servitor", 100, 50)
    assert deep.pitch < base.pitch and deep.low_shelf_db > base.low_shelf_db
    shiny = fx.preset("servitor", 50, 100)
    assert shiny.ring_mix > base.ring_mix and all(b[1] > a[1] for a, b in zip(base.combs, shiny.combs))
    assert all(fb <= 0.85 for _ms, fb in fx.preset("servitor", 100, 100).combs)
    x = tone(220, 0.5)
    assert len(fx.apply(x, RATE, "servitor", depth=100)) > len(fx.apply(x, RATE, "servitor", depth=0))
    assert fx.preset("none", 100, 100).name == "none"


def test_depth_adds_low_energy_on_a_voice_like_signal():
    t = np.arange(int(RATE * 0.6)) / RATE
    voice = sum((0.3 / k) * np.sin(2 * np.pi * 140 * k * t) for k in range(1, 12))      # harmonic stack at 140 Hz
    x = (voice / np.max(np.abs(voice)) * 20000).astype(np.int16)

    def low_share(y):
        spec = np.abs(np.fft.rfft(y.astype(np.float32)))
        freqs = np.fft.rfftfreq(len(y), 1 / RATE)
        return spec[freqs < 200].sum() / spec.sum()
    mid, deep = fx.apply(x, RATE, "servitor", depth=50), fx.apply(x, RATE, "servitor", depth=100)
    assert len(deep) > len(mid) and low_share(deep) > low_share(mid)
    assert fx.chest(fx.to_float(x), RATE, 0) is not None
