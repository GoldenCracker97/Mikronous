"""`mik` entry point.

Subcommands:
  doctor   Check that every Mikronous piece is installed and running.
  model    (Phase 0.5) Fit the local model to this machine's hardware.
  toggle   (Phase 3) Show/hide the tray chat window.
"""

from __future__ import annotations

import argparse
import sys

from . import doctor


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "model":  # hand everything after `model` to its own parser (keeps --help working)
        return _model(argv[1:])
    parser = argparse.ArgumentParser(prog="mik", description="Mikronous desktop assistant tools")
    sub = parser.add_subparsers(dest="cmd")

    p_doc = sub.add_parser("doctor", help="check install state of every component")
    p_doc.set_defaults(func=lambda a: doctor.main())

    p_model = sub.add_parser("model", help="fit the local model to this hardware (detect/list/recommend/use/tune/status/bench)")
    p_model.set_defaults(func=lambda a: _model([]))

    p_priv = sub.add_parser("privacy", help="what can leave the machine: status | offline | online")
    p_priv.add_argument("action", nargs="?", default="status", choices=["status", "offline", "online"])
    p_priv.set_defaults(func=lambda a: _privacy(a.action))

    p_tog = sub.add_parser("toggle", help="(not yet available; arrives in Phase 3)")
    p_tog.set_defaults(func=lambda a: _not_yet("toggle", "3"))

    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 1
    return args.func(args)


def _model(rest: list[str]) -> int:
    from mikronous_model.cli import main as model_main
    return model_main(rest, prog="mik model")


def _privacy(action: str) -> int:
    from . import privacy
    return privacy.main([action])


def _not_yet(name: str, phase: str) -> int:
    print(f"`mik {name}` is planned for Phase {phase} and is not implemented yet.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
