"""`operator ext` -- turn installed extensions on and off.

Activation is a file a human writes. These verbs write it. Installing a
package still does not enable anything.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .fleet import _bootstrap


def _config():
    from operator_extensions import activation
    return activation


class ConfigError(Exception):
    """The activation file exists but cannot be used as config."""


def _load() -> dict:
    path = _config().config_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ConfigError(f"could not read {path}: {exc}") from exc
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise ConfigError(f"malformed JSON in {path}") from None
    if not isinstance(parsed, dict):
        raise ConfigError(f"malformed JSON in {path}")
    return parsed


def _save(data: dict) -> bool:
    path = _config().config_path()
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return True
    except (OSError, TypeError, ValueError):
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def _discovered() -> "list[str]":
    import extensions
    found, _failures = extensions.discover()
    return [ext.name for ext in found]


def _parse_set(values: "list[str]") -> "dict | None":
    parsed: dict = {}
    for item in values:
        if "=" not in item:
            print(f"expected KEY=VALUE, got {item}", file=sys.stderr)
            return None
        key, _, raw = item.partition("=")
        if not key.strip():
            print(f"expected KEY=VALUE, got {item}", file=sys.stderr)
            return None
        try:
            parsed[key] = json.loads(raw)
        except ValueError:
            parsed[key] = raw
    return parsed


def _require_name(name: str) -> bool:
    if name in _discovered():
        return True
    print(f"not a registered extension: {name}", file=sys.stderr)
    print("see: operator ext list", file=sys.stderr)
    return False


def _entry(config: dict, name: str) -> dict:
    current = config.get(name)
    if isinstance(current, dict):
        return dict(current)
    return {}


def _list(_args) -> int:
    _bootstrap()
    names = _discovered()
    if not names:
        print("No extensions registered.")
        return 0
    try:
        config = _load()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    for name in names:
        entry = config.get(name)
        on = isinstance(entry, dict) and entry.get("enabled") is True
        state = "enabled" if on else "disabled"
        extras = []
        if isinstance(entry, dict):
            for key, value in entry.items():
                if key == "enabled":
                    continue
                extras.append(f"{key}={json.dumps(value, ensure_ascii=True)}")
        suffix = ("  " + " ".join(extras)) if extras else ""
        print(f"{name}  {state}{suffix}")
    return 0


def _enable(args) -> int:
    _bootstrap()
    if not _require_name(args.name):
        return 1
    settings = _parse_set(args.set or [])
    if settings is None:
        return 2
    try:
        config = _load()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    entry = _entry(config, args.name)
    entry["enabled"] = True
    entry.update(settings)
    config[args.name] = entry
    if not _save(config):
        print("could not write extensions.json", file=sys.stderr)
        return 1
    print(f"enabled {args.name}")
    return 0


def _disable(args) -> int:
    _bootstrap()
    try:
        config = _load()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.name not in config and args.name not in _discovered():
        print(f"not a registered extension: {args.name}", file=sys.stderr)
        print("see: operator ext list", file=sys.stderr)
        return 1
    entry = _entry(config, args.name)
    entry["enabled"] = False
    config[args.name] = entry
    if not _save(config):
        print("could not write extensions.json", file=sys.stderr)
        return 1
    print(f"disabled {args.name}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="operator ext",
        description="Enable or disable installed extensions.")
    sub = parser.add_subparsers(dest="command", required=True)

    listed = sub.add_parser("list", help="show registered extensions")
    listed.set_defaults(func=_list)

    enable = sub.add_parser("enable", help="turn an extension on")
    enable.add_argument("name")
    enable.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    enable.set_defaults(func=_enable)

    disable = sub.add_parser("disable", help="turn an extension off")
    disable.add_argument("name")
    disable.set_defaults(func=_disable)
    return parser


def main(argv: "list[str] | None" = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    sys.exit(main())
