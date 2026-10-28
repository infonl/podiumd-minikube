"""A local stand-in for Azure Key Vault: the `az keyvault secret` calls of podiumd's scripts/openbao-activate.sh.

Items are files in .openbao/keyvault/ (mode 0600), one per item name; the vault name is ignored. `az` on the
script's PATH runs this module (shim_dir); any other az call fails, so a new call in the script shows up.
"""

import argparse
import stat
import sys

from pathlib import Path

from podiumd_minikube.paths import OPENBAO_DIR

KEYVAULT_DIR = OPENBAO_DIR / "keyvault"


def item(name: str) -> Path:
    """The file of item name."""
    return KEYVAULT_DIR / name


def write(name: str, value: str) -> None:
    """Stores value as item name, readable by the owner only."""
    KEYVAULT_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = item(name)
    path.touch(mode=0o600)
    path.write_text(value, encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="az", exit_on_error=False)
    parser.add_argument("group", choices=["keyvault"])
    parser.add_argument("kind", choices=["secret"])
    parser.add_argument("action", choices=["show", "set", "set-attributes"])
    parser.add_argument("--vault-name", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--subscription")
    parser.add_argument("--query", choices=["value"])
    parser.add_argument("-o", "--output", choices=["tsv", "none"])
    parser.add_argument("--file")
    parser.add_argument("--encoding", choices=["utf-8"])
    parser.add_argument("--tags", nargs="*")
    return parser


def main(argv: list[str]) -> int:
    """Runs one `az keyvault secret show|set|set-attributes` call; 1 for a missing item or another call."""
    try:
        args = _parser().parse_args(argv)
    except (argparse.ArgumentError, SystemExit):
        print(f"az stand-in (podiumd_minikube.keyvault) does not support: az {' '.join(argv)}", file=sys.stderr)
        return 1
    path = item(args.name)
    if args.action == "set":
        if not args.file:
            print("az stand-in: `secret set` needs --file", file=sys.stderr)
            return 1
        write(args.name, Path(args.file).read_text(encoding="utf-8"))
        return 0
    if not path.is_file():
        print(f"az stand-in: no item {args.name} in {KEYVAULT_DIR}", file=sys.stderr)
        return 1
    if args.action == "show":
        print(path.read_text(encoding="utf-8"))
    return 0


def shim_dir(directory: Path) -> Path:
    """directory with an executable `az` that runs main with this Python; put it first on PATH."""
    az = directory / "az"
    az.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m podiumd_minikube.keyvault "$@"\n', encoding="utf-8")
    az.chmod(az.stat().st_mode | stat.S_IXUSR)
    return directory


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
