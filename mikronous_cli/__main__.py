"""`mik` entry point.

Subcommands:
  doctor   Check that every Mikronous piece is installed and running.
  model    Fit the local model to this machine's hardware.
  voice    Persona level: plain | light | full.
  tray     Run the tray app (chat window + hotkey target).
  toggle   Show/hide the tray chat window (starts the tray if needed).
"""

from __future__ import annotations

import argparse
import sys

from . import __version__, doctor


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "model" and len(argv) > 1 and argv[1] in ("start", "stop", "restart", "running"):
        return _model_server(argv[1])
    if argv and argv[0] == "model":  # hand everything after `model` to its own parser (keeps --help working)
        return _model(argv[1:])
    if argv and argv[0] == "ask":
        from . import ask
        return ask.main(argv[1:])
    if argv and argv[0] == "tools":
        from . import tools_cmd
        return tools_cmd.main(argv[1:])
    if argv and argv[0] == "voice":
        from . import voice
        return voice.main(argv[1:])
    if argv and argv[0] in ("tray", "toggle", "show", "hide", "quit-tray"):
        from mikronous_tray.__main__ import main as tray_main
        return tray_main(argv[1:] if argv[0] == "tray" else ["quit" if argv[0] == "quit-tray" else argv[0]])
    parser = argparse.ArgumentParser(prog="mik", description="Mikronous desktop assistant tools")
    parser.add_argument("--version", action="version", version=f"mik {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    p_doc = sub.add_parser("doctor", help="check install state of every component")
    p_doc.set_defaults(func=lambda a: doctor.main())

    p_model = sub.add_parser("model", help="fit the local model to this hardware (detect/list/recommend/use/tune/status/bench)")
    p_model.set_defaults(func=lambda a: _model([]))

    p_ask = sub.add_parser("ask", help='ask one question through the gateway: mik ask "remind me in 10 minutes to ..."')
    p_ask.set_defaults(func=lambda a: 0)

    p_tools = sub.add_parser("tools", help="toolsets the gateway exposes to the assistant (--json for raw)")
    p_tools.set_defaults(func=lambda a: 0)

    p_priv = sub.add_parser("privacy", help="what can leave the machine: status | offline | online")
    p_priv.add_argument("action", nargs="?", default="status", choices=["status", "offline", "online"])
    p_priv.set_defaults(func=lambda a: _privacy(a.action))

    p_docs = sub.add_parser("docs", help="document index for docs_search: status | reindex [--force] | search <q>")
    p_docs.add_argument("rest", nargs=argparse.REMAINDER)
    p_docs.set_defaults(func=lambda a: _docs(a.rest))

    sub.add_parser("voice", help="how much machine-priest the assistant speaks: plain | light | full (no arg: show)")
    sub.add_parser("tray", help="run the tray app (chat window, Meta+Space target, reminder inbox)")
    sub.add_parser("toggle", help="show/hide the chat window; starts the tray when it is not running")

    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 1
    return args.func(args)


def _model_server(action: str) -> int:
    """Start/stop the llama-server through the platform's runner (systemd on Linux, a process on Windows)."""
    from mikronous_model import runner
    if action == "running":
        print("running" if runner.is_running() else "stopped")
        return 0 if runner.is_running() else 1
    ok = {"start": runner.start, "stop": runner.stop, "restart": runner.restart}[action]()
    if action in ("start", "restart") and ok:
        ok = runner.wait_until_up(180, progress=lambda: print(".", end="", file=sys.stderr, flush=True))
        print("" if ok else "\nllama-server did not come up:\n" + runner.recent_log(), file=sys.stderr)
    print(f"llama-server {action}: {'ok' if ok else 'failed'}")
    return 0 if ok else 1


def _model(rest: list[str]) -> int:
    from mikronous_model.cli import main as model_main
    return model_main(rest, prog="mik model")


def _docs(rest: list[str]) -> int:
    from . import docs
    return docs.main(rest)


def _privacy(action: str) -> int:
    from . import privacy
    return privacy.main([action])


if __name__ == "__main__":
    sys.exit(main())
