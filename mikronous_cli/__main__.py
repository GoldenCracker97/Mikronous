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
    parser = argparse.ArgumentParser(prog="mik", description="Mikronous desktop assistant tools")
    sub = parser.add_subparsers(dest="cmd")

    p_doc = sub.add_parser("doctor", help="check install state of every component")
    p_doc.set_defaults(func=lambda a: doctor.main())

    for name, phase in (("model", "0.5"), ("toggle", "3")):
        p = sub.add_parser(name, help=f"(not yet available; arrives in Phase {phase})")
        p.set_defaults(func=lambda a, n=name, ph=phase: _not_yet(n, ph))

    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 1
    return args.func(args)


def _not_yet(name: str, phase: str) -> int:
    print(f"`mik {name}` is planned for Phase {phase} and is not implemented yet.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
