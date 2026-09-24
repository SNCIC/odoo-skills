#!/usr/bin/env python3
"""Report code terms no po file translates, including the ones no pot lists.

``i18n_scan.py`` reads the pot: a term the pot does not mention does not exist
for it.  ``i18n_db_audit.py`` reads the database: code terms are never stored
there, so it cannot see them either.  Both therefore miss the same class of
string -- a ``_()`` / ``_t()`` / QWeb term added to the source after the pot was
generated, or never picked up by upstream's pot bot.  In the running instance
such a term is served in English and no tool reports it: ``Shortcuts`` in the
web user menu (``user_menu_items.js``) was one, right next to ``Help`` and ``My
Preferences``, which ``web.pot`` does list and zh_CN translates.

This script takes the source as the truth.  It extracts the code terms of the
installed modules with Odoo's own babel extractors -- the same code
``odoo-bin i18n export`` runs to build a pot -- and keeps the ones that neither
the module's own po files nor the overlay deliver.  Odoo resolves a code term
from the po of the module *declaring* it, and the overlay contributes through
the runtime cache instead of through files, so both have to be asked.

It needs no database of its own: the addons path comes from the project's
configuration (``--config`` / ``--root``, see ``resolve_addons_paths``), and only
``--installed-db`` asks ``psql`` for the module list.  A database is needed by
``--installed-db`` only; without it every module of the scanned directories is
reported, which is the honest answer when nobody says what the instance runs.

Findings are not written to a worklist: a code entry needs the ``#. odoo-python``
/ ``#. odoo-javascript`` comment that tells the runtime which bundle the term
belongs to, and it is not read from the occurrence -- a wrong or missing comment
makes an entry look perfect and translate nothing.  The report gives the file and
line to write that entry by hand (or with ``i18n_set.py apply``), and
``i18n_apply.py check`` then proves the archive survived.

    scripts/i18n_code_audit.py --config /home/xfusion/etc/odoo/odoo20tbb.conf \\
        --root /home/xfusion/projects/odoo/odoo20tbb \\
        --overlay-module .../addons/sn_odoo20_translations \\
        --installed-db odoo20 --lang zh_CN --summary
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import polib

from i18n_common import (
    INTENTIONAL_FILENAME,
    iter_modules,
    read_intentional,
    read_overlay,
    resolve_addons_paths,
)
from i18n_db_audit import installed_modules, interesting
from i18n_scan import GENERATOR, category_of, fold_by_msgid, merge_with_archive, write_fragment


class _NoDatabase:
    """Enough of an Environment for the source walk.

    ``TranslationModuleReader._export_translatable_resources`` ends with the
    modules' attachment translations, which need a registry.  The walk over the
    module files needs none, and this check has no database.
    """

    class _Stub:
        @staticmethod
        def _extract_resource_attachment_translations(_module, _lang):
            return ()

    def __getitem__(self, _model_name):
        return self._Stub


def code_terms(roots: list[Path], modules: set[str], lang: str) -> list[dict]:
    """Every code term of ``modules``, with the file and line declaring it.

    Odoo's extractors are reused rather than reimplemented: they are what decides
    that a ``_t()`` call in a JS file, a QWeb template or a spreadsheet dashboard
    is a term, and a regex looking for the same thing would both invent terms and
    miss the ones hidden in a template.
    """
    from odoo.tools.translate import TranslationModuleReader  # noqa: PLC0415 - needs the source tree

    reader = object.__new__(TranslationModuleReader)
    reader._cr = None
    reader._lang = lang
    reader._to_translate = []
    reader._modules = sorted(modules)
    reader._installed_modules = sorted(modules)
    reader._path_list = [(str(root), True) for root in roots]
    reader.env = _NoDatabase()
    reader._export_translatable_resources()

    findings = []
    for module, ttype, name, _lineno, source, value, _comments in reader:
        if ttype != "code" or value.strip() or not interesting(source):
            # ``value`` is the translation of the declaring module's own po
            # files, i.e. exactly what the runtime would serve.  ``interesting``
            # is the audit's rule for what a user reads: it drops the strings
            # that are already Chinese/CJK, pure markup and control codes, which
            # an extraction of the whole source otherwise reports by the dozen
            # (a katakana sample value, a lone month character, a CSS blob).
            continue
        findings.append({
            "module": module,
            "kind": "python" if name.endswith(".py") else "javascript",
            "file": name,
            "line": _lineno,
            "term": source,
        })
    return findings


def write_worklists(out_dir: Path, lang: str, findings: list[dict]) -> int:
    """One worklist file per module, in the shape ``i18n_apply.py`` expects.

    The comment is written here rather than copied from anywhere: the runtime
    reads ``#. odoo-python`` / ``#. odoo-javascript`` to decide which bundle a
    code term belongs to, and an entry with the wrong one translates nothing
    while looking perfect.  The occurrence keeps the ``:0`` line the shipped
    pots use -- only the path matters, and a real line number would make the
    archive differ from a scan on every source edit.
    """
    by_module: dict[str, list[dict]] = {}
    for finding in findings:
        by_module.setdefault(finding["module"], []).append(finding)
    written = 0
    for module, rows in sorted(by_module.items()):
        entries = []
        for row in rows:
            entry = polib.POEntry(
                msgid=row["term"], msgstr="",
                comment=f"module: {module}\nodoo-{row['kind']}",
                occurrences=[(f"code:{row['file']}:0", "")])
            entry.category = category_of(entry)  # type: ignore[attr-defined]
            entries.append(entry)
        target = out_dir / f"{module}.po"
        entries = fold_by_msgid(entries)
        write_fragment(target, lang, merge_with_archive(target, entries, prune=False), [module],
                       generator=GENERATOR,
                       title=f"Odoo {lang} translation worklist -- {module}\n"
                             f"From the source: code terms no pot lists, so no po "
                             f"translates them. Fill in every msgstr, then merge with "
                             f"i18n_apply.py.")
        written += 1
    return written


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lang", default="zh_CN", help="language to check (default: zh_CN)")
    parser.add_argument("--module", metavar="A,B",
                        help="only these modules (default: every module of the scanned dirs)")
    parser.add_argument("--installed-db", metavar="DBNAME",
                        help="ask psql for the installed modules of this database")
    parser.add_argument("--config", metavar="FILE",
                        help="Odoo configuration file, for its addons_path")
    parser.add_argument("--root", metavar="DIR",
                        help="project directory or its odoo/ source tree")
    parser.add_argument("--addons", action="append", default=[], metavar="DIR",
                        help="extra addons directory (repeatable)")
    parser.add_argument("--overlay-module", action="append", default=[], metavar="DIR",
                        help="module whose po files carry the project's own entries (repeatable)")
    parser.add_argument("--intentional", action="append", default=[], metavar="FILE",
                        help=f"terms kept in English on purpose (default: {INTENTIONAL_FILENAME})")
    parser.add_argument("--summary", action="store_true", help="counts by module, not every term")
    parser.add_argument("--out-dir", metavar="DIR",
                        help="write one worklist per module, ready for i18n_apply.py")
    args = parser.parse_args()

    roots, how = resolve_addons_paths(
        Path(args.config).expanduser().resolve() if args.config else None,
        Path(args.root).expanduser().resolve() if args.root else None,
        [Path(raw).expanduser().resolve() for raw in args.addons])
    if how == "none":
        sys.exit("could not resolve the addons path: pass --config or --root")

    installed = installed_modules(args.installed_db) if args.installed_db else None
    modules = set(filter(None, (args.module or "").split(",")))
    if installed is not None:
        modules = (modules & installed) if modules else installed
    if not modules:
        modules = set(iter_modules(roots))

    overlay: dict[str, dict[str, str]] = {}
    for raw in args.overlay_module:
        overlay.update(read_overlay(Path(raw).expanduser().resolve() / "i18n" / f"{args.lang}.po"))

    intentional_files = [Path(raw).expanduser().resolve() for raw in args.intentional] or \
        [root / "translations" / args.lang / INTENTIONAL_FILENAME for root in roots]
    intentional = read_intentional(intentional_files)

    findings = [f for f in code_terms(roots, modules, args.lang)
                if f["term"] not in intentional
                and f["term"] not in overlay.get(f["module"], {})]

    for finding in findings:
        print(f"  {finding['module']:<24} {finding['kind']:<10} "
              f"{finding['file']}:{finding['line']:<6} {finding['term'][:60]!r}")
    by_module: dict[str, int] = {}
    for finding in findings:
        by_module[finding["module"]] = by_module.get(finding["module"], 0) + 1
    print(f"\n{len(findings)} code term(s) no po file of the scanned modules translates, "
          f"in {len(modules)} module(s), language {args.lang}", file=sys.stderr)
    if by_module and args.summary:
        print("  by module:", file=sys.stderr)
        for module, count in sorted(by_module.items(), key=lambda item: (-item[1], item[0])):
            print(f"    {module:<30} {count:>6}", file=sys.stderr)
    if not findings:
        print("  every code term of those modules is translated", file=sys.stderr)
    if args.out_dir:
        written = write_worklists(Path(args.out_dir).expanduser().resolve(), args.lang, findings)
        print(f"wrote {written} worklist file(s) to {args.out_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
