#!/usr/bin/env python3
"""Reuse the translations Odoo already ships for other modules.

Every Odoo release ships a ``<lang>.po`` per module, so the same term is often
already translated somewhere else in the tree (Weblate keeps them aligned).
This builds a translation memory out of those files and applies it to a
worklist:

    # how much of the worklist could be reused as is?
    scripts/i18n_tm.py stats translations/zh_CN --addons <addons path>

    # fill the unambiguous matches into the worklists (in place)
    scripts/i18n_tm.py fill translations/zh_CN --addons <addons path>

An entry is only filled when every module in the tree translates that msgid the
same way; terms with several variants are listed instead, so a human (or the
model) can pick the right one from the context in the worklist.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

import polib

from i18n_common import iter_modules, po_candidates


def build_memory(roots: list[Path], lang: str) -> tuple[dict[str, str], dict[str, Counter]]:
    """Return (unambiguous translations, all variants per msgid)."""
    variants: dict[str, Counter] = defaultdict(Counter)
    for _name, module_dir in iter_modules(roots):
        for path in po_candidates(module_dir, lang):
            if not path.is_file():
                continue
            for entry in polib.pofile(str(path), encoding="utf-8"):
                if entry.msgid and entry.msgstr.strip() and not entry.obsolete and not entry.msgid_plural:
                    variants[entry.msgid][entry.msgstr] += 1
    resolved = {msgid: counter.most_common(1)[0][0] for msgid, counter in variants.items()}
    return resolved, variants


def worklists(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            files.extend(sorted(path.glob("*.po")))
        elif path.is_file():
            files.append(path)
        else:
            sys.exit(f"no such file or directory: {path}")
    return files


def load(files: list[Path]) -> list[tuple[Path, polib.POFile]]:
    return [(path, polib.pofile(str(path), encoding="utf-8")) for path in files]


def cmd_stats(args) -> int:
    memory, variants = build_memory(
        [Path(root).expanduser().resolve() for root in args.addons], args.lang)
    files = load(worklists(args.paths))
    total = hits = ambiguous = 0
    for _path, po in files:
        for entry in po:
            if not entry.msgid:
                continue
            total += 1
            if entry.msgid in memory:
                hits += 1
                if len(variants[entry.msgid]) > 1:
                    ambiguous += 1
    print(f"translation memory: {len(memory)} terms from the {args.lang} po files on disk")
    print(f"worklist entries: {total}")
    print(f"exact matches: {hits} ({100 * hits / total:.1f}%), "
          f"of which {ambiguous} have more than one translation in the tree")
    return 0


def cmd_fill(args) -> int:
    memory, variants = build_memory(
        [Path(root).expanduser().resolve() for root in args.addons], args.lang)
    filled = conflicts = 0
    for path, po in load(worklists(args.paths)):
        changed = False
        for entry in po:
            if not entry.msgid or entry.msgstr.strip() or entry.msgid not in memory:
                continue
            if len(variants[entry.msgid]) > 1 and not args.all:
                conflicts += 1
                continue
            entry.msgstr = memory[entry.msgid]
            changed = True
            filled += 1
        if changed and not args.dry_run:
            po.save(str(path))
    print(f"filled {filled} entr(ies) from the translation memory")
    if conflicts:
        print(f"left {conflicts} entr(ies) alone because the tree translates them "
              f"in more than one way (use --all to take the most frequent one)")
    if args.dry_run:
        print("dry run; nothing written")
    return 0


def cmd_lookup(args) -> int:
    memory, variants = build_memory(
        [Path(root).expanduser().resolve() for root in args.addons], args.lang)
    for msgid in args.msgids:
        counter = variants.get(msgid)
        if not counter:
            print(f"{msgid!r}: no translation in the tree")
            continue
        choices = ", ".join(f"{value!r} x{count}" for value, count in counter.most_common())
        print(f"{msgid!r}: {choices}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(subparser):
        subparser.add_argument("--addons", action="append", default=[], metavar="DIR", required=True,
                               help="addons directory to read existing translations from (repeatable)")
        subparser.add_argument("--lang", default="zh_CN", help="language (default zh_CN)")

    stats = subparsers.add_parser("stats", help="report the reuse rate for some worklists")
    stats.add_argument("paths", nargs="+", metavar="PATH")
    add_common(stats)
    stats.set_defaults(func=cmd_stats)

    fill = subparsers.add_parser("fill", help="fill unambiguous matches into the worklists")
    fill.add_argument("paths", nargs="+", metavar="PATH")
    add_common(fill)
    fill.add_argument("--all", action="store_true",
                      help="also fill terms the tree translates in several ways, using the "
                           "most frequent translation")
    fill.add_argument("--dry-run", action="store_true", help="report without writing")
    fill.set_defaults(func=cmd_fill)

    lookup = subparsers.add_parser("lookup", help="show how the tree translates given terms")
    lookup.add_argument("msgids", nargs="+", metavar="MSGID")
    add_common(lookup)
    lookup.set_defaults(func=cmd_lookup)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
