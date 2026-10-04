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
    d = fx.drive(mid, 3.0)
    assert np.max(np.abs(d)) <= 1.0 + 1e-6
