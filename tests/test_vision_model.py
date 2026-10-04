"""Vision-model plumbing: projector selection, presets, llama.env and the server command line."""
from mikronous_model import presets, runner
from mikronous_model.apply import pick_mmproj
from mikronous_model.llama_env import LlamaEnv


def test_pick_mmproj_prefers_f16_over_bf16():
    files = [("Qwen3-VL-8B-Instruct-Q4_K_M.gguf", 5), ("mmproj-BF16.gguf", 2), ("mmproj-F16.gguf", 3)]
    assert pick_mmproj(files) == ("mmproj-F16.gguf", 3)
    assert pick_mmproj([("mmproj-BF16.gguf", 2)]) == ("mmproj-BF16.gguf", 2)
    assert pick_mmproj([("x-Q4_K_M.gguf", 1)]) is None


def test_vision_presets_count_the_projector():
    p = presets.find_preset("qwen3-vl-8b-instruct")
    assert p.vision and "vision" in p.tags and p.meta().file_bytes == p.approx_bytes + p.mmproj_bytes + presets.VISION_HEADROOM
    q = presets.find_preset("qwen3-8b")
    assert q.meta().file_bytes == q.approx_bytes                       # text-only: no headroom
    assert not presets.find_preset("qwen3-8b").vision


def test_env_and_command_line_carry_mmproj(tmp_path):
    env = LlamaEnv.load(tmp_path / "llama.env")
    env["LLAMA_MMPROJ"] = "/m/mmproj-F16.gguf"
    env.save()
    again = LlamaEnv.load(tmp_path / "llama.env")
    assert again["LLAMA_MMPROJ"] == "/m/mmproj-F16.gguf" and "vision  /m/mmproj-F16.gguf" in again.summary()
    cmd = runner.command_line({"LLAMA_MODEL": "m.gguf", "LLAMA_MMPROJ": "/m/mmproj-F16.gguf", "LLAMA_EXTRA_ARGS": "--rope-scale 2"})
    assert cmd[-4:] == ["--rope-scale", "2", "--mmproj", "/m/mmproj-F16.gguf"]
    assert "--mmproj" not in runner.command_line({"LLAMA_MODEL": "m.gguf"})


def test_ensure_unit_installs_and_refreshes(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "IS_WINDOWS", False)
    monkeypatch.setattr(runner.shutil, "which", lambda t: "/bin/systemctl")
    calls = []
    monkeypatch.setattr(runner, "_systemctl", lambda *a, **k: calls.append(a))
    unit_dir = tmp_path / "user"
    assert runner.unit_outdated("llama", unit_dir) is True
    assert runner.ensure_unit("llama", unit_dir) is True and calls == [("daemon-reload",)]
    installed = unit_dir / "mikronous-llama.service"
    assert "LLAMA_MMPROJ" in installed.read_text()
    assert runner.unit_outdated("llama", unit_dir) is False and runner.ensure_unit("llama", unit_dir) is False
    installed.write_text("[Service]\nExecStart=/old/llama-server\n")        # an old unit without the projector flag
    assert runner.unit_outdated("llama", unit_dir) and runner.ensure_unit("llama", unit_dir) and "LLAMA_MMPROJ" in installed.read_text()
