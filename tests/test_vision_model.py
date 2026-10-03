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
    assert p.vision and "vision" in p.tags and p.meta().file_bytes == p.approx_bytes + p.mmproj_bytes
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
