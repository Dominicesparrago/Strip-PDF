"""Strip Print Studio entry point."""

from __future__ import annotations

import argparse

from app.desktop_shell import run


def main() -> None:
    parser = argparse.ArgumentParser(description="Strip Print Studio")
    parser.add_argument("--debug", action="store_true", help="Open developer tools")
    args = parser.parse_args()
    run(debug=args.debug)


if __name__ == "__main__":
    main()
