#!/usr/bin/env python3
"""Find Odoo source terms that have no translation in a language yet.

An Odoo module ships two translation artifacts next to its source:

* ``i18n/<module>.pot`` -- every translatable term Odoo's own tooling extracted
  from the module source (python ``_()`` / ``_lt()``, JS ``_t()``, QWeb
  templates, XML data files, spreadsheet dashboards).  Upstream regenerates
  these on every commit, so the pot is the authoritative term list of the
  source at that revision.
* ``i18n/<lang>.po`` -- the translations of those terms.

Terms the pot lists but the po holds no non-empty ``msgstr`` for are exactly
the strings that show up untranslated in the UI.  This script writes those
terms out as a **.po worklist**: a valid po file whose entries carry the pot's
comments and occurrences with an empty ``msgstr``.  Fill in the ``msgstr``
fields (that file doubles as the durable archive of your translations) and
build them into the overlay module with ``i18n_apply.py``.

Translations that already live in the project's overlay module
(``--overlay-module``) count as translated: they are what the instance really
shows, so the worklist stays a list of what is genuinely missing.

Run it with the interpreter that has polib -- for these projects, the Odoo venv:

    /home/xfusion/venvs/odoo20/bin/python scripts/i18n_scan.py \
        --addons /home/xfusion/projects/odoo/odoo20tbb/odoo/addons \
        --overlay-module /home/xfusion/projects/odoo/odoo20tbb/addons/sn_odoo20_translations \
        --installed-db odoo20 --summary --out-dir /tmp/worklist
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import polib

from i18n_common import (
    ALL_CATEGORIES,
    CJK_RE,
    CONTROL_CODE_RE,
    DEFAULT_CATEGORIES,
    LETTER_RE,
    categorize,
    find_pot,
    iter_modules,
    read_intentional,
    po_candidates,
    po_header,
    read_overlay,
    read_translations,
)

GENERATOR = "i18n_scan.py (odoo-zh-i18n)"


def installed_modules(dbname: str) -> list[str]:
    command = [
        "psql", "-d", dbname, "-t", "-A", "-c",
        "SELECT name FROM ir_module_module WHERE state = 'installed' ORDER BY name",
    ]
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        sys.exit("--installed-db needs psql on PATH")
    except subprocess.CalledProcessError as exc:
        sys.exit(f"psql failed: {exc.stderr.strip()}")
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def scan_module(module: str, module_dir: Path, args, stats: dict,
                overlay: dict[str, dict[str, str]]) -> list[polib.POEntry]:
    """Untranslated pot entries of one module, as worklist entries."""
    pot_path = find_pot(module_dir, module)
    if pot_path is None:
        stats["modules_without_pot"].append(module)
        return []
    stats["modules_scanned"] += 1
    translated = read_translations(module_dir, args.lang)
    translated.update(overlay.get(module, {}))
    if not any(path.is_file() for path in po_candidates(module_dir, args.lang)):
        stats["modules_without_po"].append(module)

    wanted = set(args.categories.split(","))
    entries: list[polib.POEntry] = []
    for entry in polib.pofile(str(pot_path), encoding="utf-8"):
        msgid = entry.msgid
        if not msgid or entry.obsolete:
            continue
        if msgid in args.intentional:
            stats["skipped"]["intentional"] += 1
            stats["intentional_seen"].add(msgid)
            if msgid in translated:
                stats["intentional_superseded"].add(msgid)
            continue
        if msgid in translated:
            stats["skipped"]["already_translated"] += 1
            continue
        if CJK_RE.search(msgid):
            stats["skipped"]["already_cjk"] += 1
            continue
        if not LETTER_RE.search(msgid):
            stats["skipped"]["no_letters"] += 1
            continue
        if CONTROL_CODE_RE.search(msgid):
            stats["skipped"]["printer_control"] += 1
            continue
        if args.max_len and len(msgid) > args.max_len:
            stats["skipped"]["too_long"] += 1
            continue
        occurrences = [[ref, lineno] for ref, lineno in entry.occurrences]
        category = categorize(msgid, occurrences)
        if category not in wanted:
            stats["skipped"]["category_filtered"] += 1
            continue
        entry.msgstr = ""
        entry.flags = []
        entry.category = category  # type: ignore[attr-defined]
        entries.append(entry)

    order = {category: index for index, category in enumerate(ALL_CATEGORIES)}
    entries.sort(key=lambda item: (order[item.category], item.msgid))  # type: ignore[attr-defined]
    if args.limit:
        entries = entries[: args.limit]
    for entry in entries:
        stats["by_category"][entry.category] += 1  # type: ignore[attr-defined]
    if entries:
        stats["by_module"][module] = len(entries)
    return entries


def merge_with_archive(path: Path, fresh: list[polib.POEntry],
                       prune: bool = True) -> list[polib.POEntry]:
    """Keep everything a previous pass already translated, add the new terms.

    With ``prune``, an entry that is still empty and that the scan no longer
    produces -- the term left the pot, or a filter (category, printer control
    code, module not installed) now excludes it -- is dropped, so the archive
    stays a list of what still needs work.  Translations are never dropped.
    """
    if not path.is_file():
        return fresh
    archive = [entry for entry in polib.pofile(str(path), encoding="utf-8") if entry.msgid]
    for entry in archive:
        entry.category = category_of(entry)  # type: ignore[attr-defined]
    if prune:
        fresh_ids = {entry.msgid for entry in fresh}
        archive = [entry for entry in archive if entry.msgstr.strip() or entry.msgid in fresh_ids]
    known = {entry.msgid for entry in archive}
    merged = archive + [entry for entry in fresh if entry.msgid not in known]
    order = {category: index for index, category in enumerate(ALL_CATEGORIES)}
    merged.sort(key=lambda item: (order[getattr(item, "category", None)], item.msgid))
    return merged


def category_of(entry: polib.POEntry) -> str:
    return categorize(entry.msgid, [[ref, lineno] for ref, lineno in entry.occurrences])


def module_of(entry: polib.POEntry) -> str:
    for line in (entry.comment or "").split("\n"):
        if line.strip().startswith("module:"):
            return line.split(":", 1)[1].strip()
    return ""


def write_fragment(path: Path, lang: str, entries: list[polib.POEntry], modules: list[str]) -> None:
    title = (f"Odoo {lang} translation worklist -- {', '.join(modules)}\n"
             f"Fill in the msgstr of every entry, then merge with i18n_apply.py.")
    blocks = [po_header(lang, GENERATOR, title)]
    for entry in entries:
        blocks.append(polib.POEntry.__unicode__(entry, 78).rstrip("\n"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def print_summary(stats: dict, total: int, args) -> None:
    print(f"modules scanned: {stats['modules_scanned']}", file=args.log)
    if stats["modules_without_pot"]:
        print(f"modules without a .pot (skipped): {len(stats['modules_without_pot'])}", file=args.log)
    if stats["modules_without_po"]:
        print(f"modules with no {args.lang} po file at all: {len(stats['modules_without_po'])}",
              file=args.log)
    print("\nuntranslated terms by category:", file=args.log)
    for category, count in stats["by_category"].items():
        if count:
            print(f"  {category:<12} {count:>6}", file=args.log)
    print(f"  {'TOTAL':<12} {total:>6}", file=args.log)
    if stats["by_module"]:
        print("\nby module:", file=args.log)
        for module, count in sorted(stats["by_module"].items(), key=lambda item: -item[1]):
            print(f"  {module:<32} {count:>6}", file=args.log)
    dropped = {key: value for key, value in stats["skipped"].items() if value}
    if dropped:
        print("\nskipped: " + ", ".join(f"{key}={value}" for key, value in dropped.items()),
              file=args.log)
    if args.intentional:
        working = stats['intentional_seen'] - stats['intentional_superseded']
        print(f"\nintentional: {len(args.intentional)} declared, {len(working)} "
              f"still untranslated in the source", file=args.log)


def report_stale_intentional(stats: dict, args) -> None:
    """Declarations in _intentional.txt that no longer buy anything.

    A term the scan had to skip is a decision still standing.  One it did not
    see either left the source or is translated now -- upstream po or overlay --
    so the line can go.  Only the scan can tell: it reads the source, while the
    worklists hold what is left to do.
    """
    if not args.intentional:
        return
    superseded = stats["intentional_superseded"]
    vanished = set(args.intentional) - stats["intentional_seen"]
    if not superseded and not vanished:
        return
    if vanished and (args.limit or args.modules or args.installed_db):
        print("\nnote: this scan was restricted, so some terms below may simply be "
              "outside it", file=args.log)
    if superseded:
        print(f"\n{len(superseded)} term(s) of _intentional.txt are translated now, "
              f"drop them from the list:", file=args.log)
        for msgid in sorted(superseded):
            print(f"  {msgid!r}", file=args.log)
    if vanished:
        print(f"\n{len(vanished)} term(s) of _intentional.txt are not in the scanned "
              f"source any more, re-check them by hand:", file=args.log)
        for msgid in sorted(vanished):
            print(f"  {msgid!r}", file=args.log)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--addons", action="append", default=[], metavar="DIR",
                        help="addons directory to scan (repeatable); use every entry of "
                             "the project's addons_path")
    parser.add_argument("--lang", default="zh_CN", help="target language (default zh_CN)")
    parser.add_argument("--modules", help="comma separated module names to restrict to")
    parser.add_argument("--installed-db", metavar="DBNAME",
                        help="restrict to the modules installed in this database (uses psql)")
    parser.add_argument("--exclude-modules", metavar="A,B", help="modules to skip")
    parser.add_argument("--categories", default=",".join(DEFAULT_CATEGORIES),
                        metavar="LIST", help="subset of "
                             f"{','.join(ALL_CATEGORIES)} (default {','.join(DEFAULT_CATEGORIES)})")
    parser.add_argument("--max-len", type=int, default=0,
                        help="skip msgids longer than this many characters")
    parser.add_argument("--limit", type=int, default=0,
                        help="keep at most N terms per module (for a first batch)")
    parser.add_argument("--out-dir", metavar="DIR",
                        help="write one worklist per module to DIR/<module>.po "
                             "(existing translations in those files are preserved)")
    parser.add_argument("--out", metavar="FILE", help="write a single combined worklist")
    parser.add_argument("--overlay-module", metavar="DIR", action="append", default=[],
                        help="directory of the project's translations overlay module; its "
                             "i18n/<lang>.po entries count as already translated (repeatable)")
    parser.add_argument("--intentional", action="append", default=[], metavar="FILE",
                        help="file of msgids deliberately kept in English, one per line "
                             "with an optional '# reason' (repeatable); a "
                             "_intentional.txt in --out-dir is read automatically")
    parser.add_argument("--summary", action="store_true", help="print the summary")
    args = parser.parse_args()
    args.intentional = read_intentional(
        [Path(raw).expanduser().resolve() for raw in args.intentional]
        + ([Path(args.out_dir).expanduser().resolve()] if args.out_dir else []))

    if not args.addons:
        parser.error("--addons is required (give every directory of addons_path)")
    unknown = set(args.categories.split(",")) - set(ALL_CATEGORIES)
    if unknown:
        parser.error(f"unknown categories: {', '.join(sorted(unknown))}")
    if args.out_dir and args.out:
        parser.error("use either --out-dir or --out, not both")
    args.log = sys.stderr if (args.out_dir or args.out) else sys.stdout

    only = set(filter(None, (args.modules or "").split(",")))
    if args.installed_db:
        only |= set(installed_modules(args.installed_db))
    exclude = set(filter(None, (args.exclude_modules or "").split(",")))

    stats = {
        "modules_scanned": 0,
        "modules_without_pot": [],
        "modules_without_po": [],
        "skipped": {"already_translated": 0, "already_cjk": 0, "no_letters": 0,
                    "printer_control": 0, "too_long": 0, "category_filtered": 0,
                    "intentional": 0},
        "by_category": {category: 0 for category in ALL_CATEGORIES},
        "by_module": {},
        "intentional_seen": set(),
        "intentional_superseded": set(),
    }

    overlay: dict[str, dict[str, str]] = {}
    for module_dir in args.overlay_module:
        path = Path(module_dir).expanduser().resolve() / "i18n" / f"{args.lang}.po"
        found = read_overlay(path)
        if not found:
            print(f"note: {path} has no entries for {args.lang}", file=args.log)
        for name, translations in found.items():
            overlay.setdefault(name, {}).update(translations)

    all_entries: list[polib.POEntry] = []
    for module, module_dir in iter_modules(
            [Path(root).expanduser().resolve() for root in args.addons], only, exclude):
        entries = scan_module(module, module_dir, args, stats, overlay)
        if args.out_dir:
            if entries:
                target = Path(args.out_dir) / f"{module}.po"
                write_fragment(target, args.lang,
                               merge_with_archive(target, entries, prune=not args.limit),
                               [module])
            continue
        all_entries.extend(entries)

    total = sum(stats["by_module"].values())
    report_stale_intentional(stats, args)
    if args.out_dir:
        print(f"wrote {len(stats['by_module'])} worklist file(s) to {args.out_dir}", file=args.log)
    elif all_entries:
        all_entries.sort(key=lambda item: (module_of(item), item.msgid))
        if args.out:
            write_fragment(Path(args.out), args.lang, all_entries, sorted(stats["by_module"]))
            print(f"wrote {len(all_entries)} terms to {args.out}", file=args.log)
        else:
            print(po_header(args.lang, GENERATOR, "Odoo translation worklist"))
            for entry in all_entries:
                print()
                print(polib.POEntry.__unicode__(entry, 78).rstrip("\n"))
    if args.summary or not (args.out or args.out_dir):
        print_summary(stats, total, args)


if __name__ == "__main__":
    main()
