#!/usr/bin/env python3
"""Fill worklist entries from a translation map.

Hand-writing a batch of translations as a script is the recommended way to
translate more than a handful of entries, but a typo in a long msgid (mail
templates, error messages) silently produces a "translation" nobody uses.  This
script removes that failure mode: the map is keyed by a *unique substring* of
the msgid, so it stays readable, and every key is resolved against the worklist
before anything is written.  A key that matches no entry, or more than one, is
reported and the run fails.

    scripts/i18n_set.py apply <worklist dir> --map batch.json
    scripts/i18n_set.py apply <worklist dir> --map batch.json --dry-run

The map is JSON, one object per module::

    {
      "product": {
        "Increase quantity": "增加数量",
        "product: %(product_name)s with variant": "产品：%(product_name)s，变体：%(variant_name)s"
      }
    }

Keys that are already translated are skipped (so a map can be re-applied), and
entries that keep an empty ``msgstr`` stay in the worklist for the next pass.
Run ``i18n_apply.py check`` afterwards.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import polib


def read_map(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        sys.exit(f"{path}: the map must be a JSON object of {{module: {{key: msgstr}}}}")
    for module, pairs in data.items():
        if not isinstance(pairs, dict):
            sys.exit(f"{path}: {module!r} must map a key to a msgstr, not {type(pairs).__name__}")
    return data


def resolve(entries: list[polib.POEntry], key: str) -> tuple[polib.POEntry | None, list[str]]:
    """The single untranslated entry a map key refers to, plus the candidates."""
    exact = [entry for entry in entries if not entry.msgstr and entry.msgid == key]
    if exact:
        return exact[0], [entry.msgid for entry in exact]
    partial = [entry for entry in entries if not entry.msgstr and key in entry.msgid]
    if len(partial) == 1:
        return partial[0], [partial[0].msgid]
    return None, [entry.msgid for entry in partial]


def cmd_apply(args) -> int:
    directory = Path(args.directory).expanduser().resolve()
    mapping = read_map(Path(args.map).expanduser())
    filled = failed = 0
    for module, pairs in mapping.items():
        path = directory / f"{module}.po"
        if not path.is_file():
            print(f"ERROR {module}: no worklist at {path}", file=sys.stderr)
            failed += len(pairs)
            continue
        po = polib.pofile(str(path))
        entries = [entry for entry in po if entry.msgid and not entry.obsolete]
        done = {entry.msgid: entry.msgstr for entry in entries if entry.msgstr}
        changed = False
        for key, msgstr in pairs.items():
            if key in done:
                print(f"skip  {module}: already translated: {key[:60]!r}")
                continue
            entry, candidates = resolve(entries, key)
            if entry is None:
                if candidates:
                    print(f"ERROR {module}: {key[:60]!r} matches {len(candidates)} entries: "
                          + " | ".join(repr(msgid[:60]) for msgid in candidates[:4]),
                          file=sys.stderr)
                else:
                    print(f"ERROR {module}: no untranslated entry matches {key[:60]!r}",
                          file=sys.stderr)
                failed += 1
                continue
            if msgstr == entry.msgid:
                print(f"WARN  {module}: {key[:50]!r} is left in English on purpose")
            entry.msgstr = msgstr
            entry.flags = []
            changed = True
            filled += 1
            print(f"set   {module}: {entry.msgid[:60]!r} -> {msgstr[:50]!r}")
        if changed and not args.dry_run:
            po.save(str(path))
    print(f"\n{filled} entry(ies) filled, {failed} key(s) failed"
          + (" (dry run, nothing written)" if args.dry_run else ""))
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)
    apply_parser = subparsers.add_parser(
        "apply", help="fill worklist entries from a JSON map")
    apply_parser.add_argument("directory", metavar="DIR",
                              help="directory holding the <module>.po worklists")
    apply_parser.add_argument("--map", required=True, metavar="FILE",
                              help="JSON file: {module: {msgid or unique substring: msgstr}}")
    apply_parser.add_argument("--dry-run", action="store_true",
                              help="report what would be filled, write nothing")
    apply_parser.set_defaults(func=cmd_apply)
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
