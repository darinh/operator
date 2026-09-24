"""`operator project` -- the door to the catalog `remember` already reads.

`paths.catalog_guid` and `paths.catalog_rows` are the only readers. A row this
module writes that those functions will not parse is a registration that
looks successful and still leaves `remember` with nowhere to write.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import uuid
from pathlib import Path

from .fleet import _bootstrap


def _rows(catalog: Path) -> "list[tuple[str, str]] | None":
    """Valid (path, guid) pairs, or None when the catalog could not be read."""
    import paths

    found: list[tuple[str, str]] = []
    try:
        with open(catalog, "r", encoding="utf-8", errors="replace",
                  newline="") as fh:
            for row in paths.catalog_rows(fh):
                if row is None or len(row) < 2:
                    continue
                path, guid = row[0].strip().strip('"'), row[1].strip().strip('"')
                if path and paths.guid_is_usable(guid):
                    found.append((path, guid))
    except FileNotFoundError:
        return []
    except OSError:
        return None
    return found


def _write_rows(catalog: Path, rows: "list[tuple[str, str]]") -> bool:
    catalog.parent.mkdir(parents=True, exist_ok=True)
    tmp = catalog.with_name(f"{catalog.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh, lineterminator="\n")
            for path, guid in rows:
                writer.writerow([path, guid])
        os.replace(tmp, catalog)
        return True
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def _resolve(given: "str | None", *, must_exist: bool = True) -> "Path | None":
    import paths

    target = Path(given) if given else Path.cwd()
    try:
        exists = target.is_dir()
    except OSError as exc:
        print(f"could not resolve {target}: {exc}", file=sys.stderr)
        return None
    if must_exist and not exists:
        print(f"not a directory: {target}", file=sys.stderr)
        return None
    try:
        if exists:
            return paths.primary_repo_root(target).resolve()
        return target.resolve()
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"could not resolve {target}: {exc}", file=sys.stderr)
        return None


def _same_path(left: Path, right: str) -> bool:
    from config import IS_WINDOWS

    candidates = [right]
    try:
        candidates.append(str(Path(right).resolve()))
    except (OSError, ValueError, RuntimeError):
        pass
    a = str(left)
    for b in candidates:
        if IS_WINDOWS:
            if a.lower() == b.lower():
                return True
        elif a == b:
            return True
    return False


def ensure_registered(path: "str | None" = None) -> "tuple[int, str, bool]":
    """Register `path` (default cwd). Returns (exit, guid, created)."""
    _bootstrap()
    import paths

    target = _resolve(path)
    if target is None:
        return 2, "", False
    found = paths.catalog_guid(target)
    if found.undecided:
        print("could not read the project catalog", file=sys.stderr)
        return 1, "", False
    if found.guid:
        return 0, found.guid, False
    guid = str(uuid.uuid4())
    catalog = paths.project_catalog_path()
    rows = _rows(catalog)
    if rows is None:
        print("could not read the project catalog", file=sys.stderr)
        return 1, "", False
    rows.append((str(target), guid))
    if not _write_rows(catalog, rows):
        print("could not write the project catalog", file=sys.stderr)
        return 1, "", False
    try:
        paths.project_dir(guid).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"could not create the project directory: {exc}", file=sys.stderr)
        return 1, "", False
    check = paths.catalog_guid(target)
    if check.guid != guid:
        print("wrote a catalog row the existing reader did not accept",
              file=sys.stderr)
        return 1, "", False
    return 0, guid, True


def _register(args) -> int:
    rc, guid, _created = ensure_registered(args.path)
    if rc == 0:
        print(guid)
    return rc


def _list(_args) -> int:
    _bootstrap()
    import paths

    rows = _rows(paths.project_catalog_path())
    if rows is None:
        print("could not read the project catalog", file=sys.stderr)
        return 1
    if not rows:
        print("No registered projects.")
        return 0
    current = paths.catalog_guid(Path.cwd())
    for path, guid in rows:
        mark = "  (current)" if current.guid == guid else ""
        print(f"{guid}  {path}{mark}")
    return 0


def _forget(args) -> int:
    _bootstrap()
    import paths

    target = _resolve(args.path, must_exist=False)
    if target is None:
        return 2
    catalog = paths.project_catalog_path()
    rows = _rows(catalog)
    if rows is None:
        print("could not read the project catalog", file=sys.stderr)
        return 1
    kept = []
    removed = None
    for path, guid in rows:
        if removed is None and _same_path(target, path):
            removed = (path, guid)
            continue
        kept.append((path, guid))
    if removed is None:
        print(f"not a registered project: {target}", file=sys.stderr)
        print("register it with: operator project register", file=sys.stderr)
        return 1
    if not _write_rows(catalog, kept):
        print("could not write the project catalog", file=sys.stderr)
        return 1
    print(f"forgot {removed[1]}")
    print("journal left on disk")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="operator project",
        description="Register a directory so a seat can remember it.")
    sub = parser.add_subparsers(dest="command", required=True)

    register = sub.add_parser("register",
                              help="register a directory (default: cwd)")
    register.add_argument("path", nargs="?",
                          help="directory to register (default: current)")
    register.set_defaults(func=_register)

    listed = sub.add_parser("list", help="show registered projects")
    listed.set_defaults(func=_list)

    forget = sub.add_parser("forget",
                            help="remove a registration; keep the journal")
    forget.add_argument("path", help="directory to forget")
    forget.set_defaults(func=_forget)
    return parser


def main(argv: "list[str] | None" = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    sys.exit(main())
