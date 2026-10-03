from mikronous_model import runner


def test_command_line_from_env():
    env = {"LLAMA_SERVER": "/opt/llama/bin/llama-server", "LLAMA_PORT": "8081", "LLAMA_MODEL": "/m/q.gguf",
           "LLAMA_ALIAS": "mikronous-local", "LLAMA_CTX": "65536", "LLAMA_NGL": "99", "LLAMA_THREADS": "8",
           "LLAMA_KV_K": "q8_0", "LLAMA_KV_V": "q4_0", "LLAMA_PARALLEL": "1",
           "LLAMA_EXTRA_ARGS": "--rope-scaling yarn --yarn-orig-ctx 32768"}
    cmd = runner.command_line(env)
    assert cmd[:5] == ["/opt/llama/bin/llama-server", "--host", "127.0.0.1", "--port", "8081"]
    assert "-ctk" in cmd and cmd[cmd.index("-ctk") + 1] == "q8_0"
    assert cmd[-4:] == ["--rope-scaling", "yarn", "--yarn-orig-ctx", "32768"]
    assert "--jinja" in cmd and cmd[cmd.index("-fa") + 1] == "on"


def test_mode_and_hint_are_consistent():
    assert runner.mode() in ("systemd", "process")
    assert "mikronous-llama" in runner.restart_hint() or "mik model restart" in runner.restart_hint()
