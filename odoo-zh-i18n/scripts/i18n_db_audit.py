#!/usr/bin/env python3
"""Audit what a running Odoo instance still serves in English.

``i18n_scan.py`` reads the source: the pot says which terms exist, the po files
say which are translated, and a term nobody translated is missing from both.
That answers "what does the source have that no translation covers" -- but not
"what does this instance actually show".  The two can disagree:

* a module ships no pot at all, so a po-based scan cannot even list its terms;
* the shipped pot is older than the source (a patch, a pull in progress);
* the po holds a translation whose ``msgstr`` the database never received, or
  whose term does not match the stored value (whitespace, HTML entities);
* the value is a view arch or a mail body whose zh_CN column exists but still
  carries English text nodes (Odoo merges translations node by node, so a view
  can be half translated).

This script answers the instance side.  It walks every translatable field of the
database, reads the English and the target-language value of each record that
carries an external id (that is the set a po file can address), and reports the
text nodes that are still English.  Findings come with the occurrence a po entry
needs (``model:`` / ``model_terms:`` plus the xmlid and the module), so
``--out-dir`` can turn them straight into worklists for the usual
``check``/``build`` flow.

Each finding is then classified by what a po can do about it, which is the point
of the exercise -- "still English" has four different answers:

* *translated nowhere yet*: nobody translated the text.  Fresh work, and the only
  tool that sees it when no pot mentions the term (a module without a pot, a
  field label the pot never listed, a value that came from a patch).
* *translated somewhere, but no po entry points at the record*: the same English
  text is translated for another record, and a translation reaches a record only
  through an entry whose occurrence names its xmlid.  The overlay has to add the
  occurrence; ``--out-dir`` prefills the known wording, so this is review work
  rather than translation work.
* *already in a po that names the record*: the po is fine and the instance has
  not imported it (module not upgraded since the po changed).  Reported with the
  po file that holds the entry, so the fix is ``-u <module>``.
* *the po names the record with a source text older than the record's value*: a
  po entry carries the record's xmlid, but its ``msgid`` is the text the record
  held before an upstream edit, so the pair (occurrence, msgid) never matches.
  A whole-value field (``char``/``text``/``name``/``help``, the ``model:``
  rows) is imported on its xmlid alone, without comparing the text, so an
  upgrade still writes that wording -- reported with the po file and with the
  wording flagged, because it can describe a quantity the text no longer
  mentions.  Markup (``model_terms:``) is matched node by node, so a stale entry
  there reaches nothing and the term stays ordinary work.
* *the po ships the English text as its translation* (``msgstr`` == ``msgid``):
  the entry points at the record, but importing it -- now or after an upgrade --
  puts the same English back.  That is a decision to review rather than work:
  either the term should be translated after all (then the overlay is the fix)
  or it belongs to the terms this project keeps in English.  Counting these as
  "translated" is what makes a po-based coverage number look better than the
  instance; counting them as "work" buries the real gaps under symbols, brand
  names and demo values.

    scripts/i18n_db_audit.py --db odoo20 --summary
    scripts/i18n_db_audit.py --db odoo20 --module base --out-dir translations/zh_CN

Run it with the Odoo venv python, from the source tree (``--root`` points at it
when you are elsewhere) so that Odoo's own HTML-node helpers can be imported --
they are what makes the node-by-node comparison of a view arch exact.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import re
import subprocess
import sys
from pathlib import Path

import polib

from i18n_common import (
    CJK_RE,
    INTENTIONAL_FILENAME,
    LETTER_RE,
    exposes_odoo_package,
    iter_modules,
    po_candidates,
    read_intentional,
    resolve_addons_paths,
)
from i18n_scan import category_of, fold_by_msgid, merge_with_archive, write_fragment

GENERATOR = "i18n_db_audit.py (odoo-zh-i18n)"
# A term that is only a placeholder, a number or punctuation needs no
# translation; the scan skips those too (``no_letters``/``already_cjk``).
NOISE_RE = re.compile(r"^(?:[\W\d_]|&[a-zA-Z]+;|<[^>]+>)*$")
# Attributes Odoo's own extractor treats as translatable text (``TRANSLATED_ATTRS``
# in ``odoo.tools.translate``, mirrored by ``translatableAttributes`` in OWL).  A
# node whose only readable text sits in one of them -- ``<i title="Position"/>``,
# an icon tooltip -- looks exactly like pure markup, and dropping it as noise
# hides every tooltip of a form.  The list is copied rather than imported so that
# the audit keeps working when Odoo cannot be imported (the caller then gets the
# same terms as the po-based scan, which never had this filter).
TRANSLATABLE_ATTR_RE = re.compile(
    r"(?:alt|aria-label|aria-placeholder|aria-roledescription|aria-valuetext|"
    r"data-tooltip|label|placeholder|title)\s*=\s*[\"'][^\"']*[A-Za-z]")
# The long Apps-store blurb of a module, which no po file of any language
# translates: opt in with --with-module-description when it is wanted anyway.
# ``summary`` and ``shortdesc`` are NOT here -- the instance imports them from
# the po entry naming ``base.module_<name>`` and renders them in the Apps list,
# so they are ordinary one-line data terms (and the modules whose po entry is
# empty are a genuine gap).
MODULE_DESCRIPTION_FIELDS = {("ir.module.module", "description")}
# ``summary`` / ``shortdesc`` of the modules the instance has installed: the
# ones upstream left English are a real gap in the Apps list.  The 557 modules
# that are merely *available* keep their English blurb in every language
# (upstream translates 42 of 557), so reporting those would bury the work under
# a decision Odoo already took.
MODULE_STORE_FIELDS = {("ir.module.module", "summary"),
                       ("ir.module.module", "shortdesc")}


def psql(dbname: str, sql: str) -> list[list[str]]:
    """Run one query and parse the result.

    ``--csv`` rather than a separator: a view arch or a help text holds newlines,
    which would break a line-per-row format.  csv.reader handles them.
    """
    completed = subprocess.run(
        ["psql", "-d", dbname, "-t", "-X", "--no-psqlrc", "--csv", "-c", sql],
        capture_output=True, text=True)
    if completed.returncode:
        sys.exit(f"psql failed: {completed.stderr.strip()}")
    return [row for row in csv.reader(completed.stdout.splitlines(keepends=True)) if row]


def translatable_fields(dbname: str) -> list[tuple[str, str, str]]:
    """(model, field, translation kind) of every translatable text field.

    Odoo 19 stores the translation strategy in ``ir_model_fields.translate``
    (``standard`` for plain text, ``html_translate`` for markup), and only the
    strategy tells whether a value has to be compared node by node.
    """
    rows = psql(dbname, """
        SELECT f.model, f.name, f.translate
        FROM ir_model_fields f
        WHERE f.translate NOT IN ('', 'False', 'false')
          AND f.ttype IN ('char', 'text', 'html')
        ORDER BY f.model, f.name
    """)
    return [(model, name, kind) for model, name, kind in rows]


def columns_of(dbname: str) -> set[tuple[str, str]]:
    """jsonb columns, i.e. the stored side of a translatable field."""
    rows = psql(dbname, """
        SELECT table_name, column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND data_type = 'jsonb'
    """)
    return {(table, column) for table, column in rows}


def translatable_other_types(dbname: str) -> list[str]:
    """Translatable fields of a type this audit does not read.

    ``audit`` covers char/text/html, which is where Odoo puts UI text; anything
    else carrying ``translate`` would be a field this report cannot see, and
    saying so is cheaper than guessing that there is none.
    """
    rows = psql(dbname, """
        SELECT model || '.' || name
        FROM ir_model_fields
        WHERE translate NOT IN ('', 'False', 'false')
          AND ttype NOT IN ('char', 'text', 'html')
        ORDER BY model, name
    """)
    return [row[0] for row in rows]


def placeholder_for_odoo() -> None:
    """Make ``import odoo`` work when the caller is in the source tree."""
    for candidate in (Path.cwd(), Path.cwd() / "odoo"):
        if exposes_odoo_package(candidate) and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))


def terms_of(value: str, kind: str) -> list[str]:
    """The translatable terms of one value, in document order.

    For markup this uses Odoo's own ``html_translate``: it walks the arch
    exactly like the importer does (text nodes plus translatable attributes such
    as ``title``/``placeholder``), so a term found here is a term a po entry can
    translate.  For plain text the value itself is the term.
    """
    if not value:
        return []
    from odoo.tools.translate import FIELD_TRANSLATE  # noqa: PLC0415 - needs the source tree

    handler = FIELD_TRANSLATE.get(kind)
    if handler in (True, False, None):
        # 'standard': the value itself is the term.
        return [value]
    found: list[str] = []
    try:
        handler(lambda term: found.append(term) or term, value)
    except Exception:  # malformed markup: Odoo keeps the source value too
        return [value]
    return found


def interesting(term: str) -> bool:
    """Whether a term is text a user reads, and not already Chinese.

    A term that is nothing but markup is not text -- unless the markup carries a
    translatable attribute, in which case the attribute value is what the user
    reads (and what the translation has to change).
    """
    if not term or not LETTER_RE.search(term) or CJK_RE.search(term):
        return False
    if NOISE_RE.match(term):
        return bool(TRANSLATABLE_ATTR_RE.search(term))
    return True


def gaps_in(en_value: str, zh_value: str | None, kind: str) -> list[str]:
    """English terms of one record that the translation does not cover.

    The two term lists line up position by position when the stored translation
    was built from the same source value (that is what ``field.translate``
    does), and a term the two share is a term nobody translated.  When the
    counts differ the stored value belongs to an older revision of the record
    (a view the upstream source has since changed), so the positions mean
    nothing and only the terms that are literally still there count as gaps --
    the source text being present in the translated value is by definition the
    untranslated case, whether or not the surrounding nodes line up.
    """
    source = [term for term in terms_of(en_value, kind) if interesting(term)]
    if not source:
        return []
    if not zh_value:
        return source
    translated = terms_of(zh_value, kind)
    if len(translated) == len(source):
        # Odoo merges node by node, so the two lists line up.
        return [term for term, target in zip(source, translated) if term == target]
    still_english = set(translated)
    return [term for term in source if term in still_english]


def audit(dbname: str, lang: str, modules: set[str], with_user_data: bool,
          limit: int, intentional: dict[str, str],
          with_module_description: bool = False) -> list[dict]:
    available = columns_of(dbname)
    findings: list[dict] = []
    for model, field, kind in translatable_fields(dbname):
        if (model, field) in MODULE_DESCRIPTION_FIELDS and not with_module_description:
            continue
        table = model.replace(".", "_")
        if (table, field) not in available:
            continue
        where_module = "" if with_user_data else \
            "AND d.module NOT IN ('__export__', 'studio_customization')"
        if (model, field) in MODULE_STORE_FIELDS:
            # ``d.module`` of a module record is always ``base``; the module the
            # blurb describes is the record's own name.
            where_module += (" AND r.name IN (SELECT name FROM ir_module_module"
                             " WHERE state = 'installed')")
        join = "JOIN ir_model_data d ON d.model = %s AND d.res_id = r.id" % quote(model)
        if with_user_data:
            # LEFT JOIN keeps records a user created; they have no module to
            # attribute a translation to, which the report shows as "?".
            join = "LEFT JOIN ir_model_data d ON d.model = %s AND d.res_id = r.id" % quote(model)
        sql = f"""
            SELECT COALESCE(d.module, '?'), COALESCE(d.name, ''), r.id,
                   r.{field}->>'en_US', COALESCE(r.{field}->>'zh_CN', ''),
                   COALESCE(d.noupdate, false)
            FROM {table} r
            {join}
            WHERE jsonb_typeof(r.{field}) = 'object'
              AND COALESCE(r.{field}->>'en_US', '') <> ''
              AND COALESCE(r.{field}->>'zh_CN', '') <> COALESCE(r.{field}->>'en_US', '')
              {where_module}
            LIMIT {limit}
        """
        try:
            rows = psql(dbname, sql)
        except SystemExit as error:  # a table that vanished: keep going
            print(f"skipped {model}.{field}: {error}", file=sys.stderr)
            continue
        for module, xmlid, res_id, en_value, zh_value, noupdate in rows:
            if modules and module not in modules:
                continue
            for term in gaps_in(en_value, zh_value, kind):
                if term in intentional:
                    continue
                findings.append({
                    "module": module,
                    "model": model,
                    "field": field,
                    "res_id": res_id,
                    "xmlid": xmlid,
                    "kind": kind,
                    "term": term,
                    # ir_model_data.noupdate: a po entry never reaches such a
                    # record (TranslationImporter.save only overwrites one with
                    # force_overwrite, which no module upgrade passes).
                    "noupdate": noupdate in (True, 't'),
                    # 'standard' is a whole-value translation, which a po entry
                    # addresses with 'model:'.  Everything else is translated
                    # term by term (html_translate, and xml_translate for
                    # ir.ui.view.arch_db), which needs 'model_terms:' -- and the
                    # importer ignores a 'model:' row for such a field, so
                    # getting this wrong makes the worklist a silent no-op.
                    "occurrence": (f"model:{model},{field}:{module}.{xmlid}"
                                   if kind == "standard"
                                   else f"model_terms:{model},{field}:{module}.{xmlid}"),
                })
    return findings


def quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def installed_modules(dbname: str) -> set[str]:
    """Modules whose po files the instance actually imports."""
    rows = psql(dbname, "SELECT name FROM ir_module_module WHERE state = 'installed'")
    return {row[0] for row in rows}


def closest_msgid(candidates: dict[str, str], term: str) -> str:
    """Of the entries naming a record, the one whose text is closest to ``term``.

    The record was edited upstream since the po was generated, so no entry
    matches it exactly; the nearest one is the wording the upgrade is about to
    write, and the one a translator should compare against the new text.
    """
    return max(candidates,
               key=lambda msgid: difflib.SequenceMatcher(None, term, msgid).ratio())


def index_po(terms: dict[str, tuple[str, str]],
             deliveries: dict[tuple[str, str], str], path: Path,
             kept_english: dict[tuple[str, str], str] | None = None,
             pointing: dict[str, dict[str, str]] | None = None) -> None:
    """Add one po file to the indexes built by ``po_index``.

    An entry whose ``msgstr`` is its own ``msgid`` goes to ``kept_english``
    (when the caller asked for that index) and to nothing else.  Odoo's own po
    files carry hundreds of them -- a brand, a demo value, a symbol, a formula
    such as ``PDF`` or ``X`` -- and they are a translator's decision, not a
    translation: Odoo's importer stores the English text it is told to store,
    so counting such an entry as "translated" would both hide the record and
    prefill a worklist with a value identical to its own msgid.

    ``pointing`` -- ``{occurrence: {msgid: po file}}`` -- keeps every entry that
    names a record, whatever its ``msgid`` is.  An entry whose ``msgid`` is not
    the record's current text is invisible to ``deliveries``; for a whole-value
    field it still gets imported, because the importer keys such a row on the
    xmlid alone (``TranslationImporter._load``, ``field.translate is True``).
    """
    for entry in polib.pofile(str(path), encoding="utf-8"):
        if not entry.msgid or entry.obsolete or not entry.msgstr.strip():
            continue
        if pointing is not None:
            for occurrence, _offset in entry.occurrences:
                if occurrence.startswith(("model:", "model_terms:")):
                    pointing.setdefault(occurrence, {})[entry.msgid] = str(path)
        kept = entry.msgstr.strip() == entry.msgid.strip()
        if kept and kept_english is None:
            continue
        if kept:
            for occurrence, _offset in entry.occurrences:
                if occurrence.startswith(("model:", "model_terms:")):
                    kept_english.setdefault((occurrence, entry.msgid), str(path))  # type: ignore[union-attr]
            continue
        terms.setdefault(entry.msgid, (entry.msgstr, str(path)))
        for occurrence, _offset in entry.occurrences:
            if occurrence.startswith(("model:", "model_terms:")):
                deliveries.setdefault((occurrence, entry.msgid), str(path))


def po_index(addons: list[Path], lang: str, overlay: list[Path],
             installed: set[str]) -> tuple[dict[str, tuple[str, str]],
                                            dict[tuple[str, str], str],
                                            dict[tuple[str, str], str],
                                            dict[str, dict[str, str]]]:
    """What the po files of the installed modules can deliver.

    Four questions have to be told apart, and only the occurrences answer the
    last three:

    * ``terms`` -- ``{msgid: (msgstr, po file)}``: is this English text
      translated *somewhere*?  An empty answer means nobody translated it yet,
      which is the work a po scan cannot even see when the module ships no pot.
    * ``deliveries`` -- ``{(occurrence, msgid): po file}``: does a po entry
      point at *this record*?  A translation only reaches a record through an
      occurrence naming its xmlid (and, for markup, the exact node text), so a
      term that is translated but never for this record still leaves the record
      English -- the overlay has to add the occurrence.  When the pair is here,
      on the other hand, the po is not at fault: the instance simply has not
      imported it (module not upgraded since).
    * ``kept_english`` -- ``{(occurrence, msgid): po file}``: the entry that
      names the record says the English text *is* the translation (``msgstr``
      equals ``msgid``).  Importing or upgrading that po changes nothing on
      screen, so this is neither translation work nor an import problem: it is
      a decision to review -- either the term really should be translated (then
      the po is wrong and the overlay is the way to fix it) or it belongs to
      the list of terms the project keeps in English, like ``_intentional.txt``
      holds for the scan.
    * ``pointing`` -- ``{occurrence: {msgid: po file}}``: every entry naming the
      record, whatever text it carries.  It is what tells a term nobody
      translated apart from a term whose entry predates an upstream edit of the
      record: the second one is imported anyway on a whole-value field, with the
      wording of the older text.

    Only installed modules are indexed: an uninstalled module's po is never
    imported, so trusting it would hide work behind a translation that can never
    arrive.  The overlay po files are indexed too -- they belong to an installed
    module, and it is often the one that needs the upgrade.
    """
    terms: dict[str, tuple[str, str]] = {}
    deliveries: dict[tuple[str, str], str] = {}
    kept_english: dict[tuple[str, str], str] = {}
    pointing: dict[str, dict[str, str]] = {}
    directories = dict(iter_modules(addons))
    for module in sorted(directories):
        if module not in installed:
            continue
        for path in po_candidates(directories[module], lang):
            if path.is_file():
                index_po(terms, deliveries, path, kept_english, pointing)
    for path in overlay:
        if path.is_file():
            index_po(terms, deliveries, path, kept_english, pointing)
    return terms, deliveries, kept_english, pointing


def write_worklists(out_dir: Path, lang: str, findings: list[dict],
                    prefill: dict[str, str] | None = None) -> int:
    """One worklist file per module, ready for the check/build flow."""
    prefill = prefill or {}
    by_module: dict[str, list[dict]] = {}
    for finding in findings:
        by_module.setdefault(finding["module"], []).append(finding)
    written = 0
    for module, rows in sorted(by_module.items()):
        entries = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            key = (row["term"], row["occurrence"])
            if key in seen:
                continue
            seen.add(key)
            # Keep where a prefilled wording comes from: the reviewer has to see
            # that the Chinese was written for another context.
            origin = f"\nfrom po: {row['term_source']}" if row.get("term_source") else ""
            entry = polib.POEntry(
                msgid=row["term"], msgstr=prefill.get(row["term"], ""),
                comment=f"module: {module}{origin}",
                occurrences=[(row["occurrence"], "")])
            # Same ordering rules as a scan worklist, so a merge into an
            # existing archive keeps one stable layout.
            entry.category = category_of(entry)  # type: ignore[attr-defined]
            entries.append(entry)
        target = out_dir / f"{module}.po"
        entries = fold_by_msgid(entries)
        write_fragment(target, lang, merge_with_archive(target, entries, prune=False), [module],
                       generator=GENERATOR,
                       title=f"Odoo {lang} translation worklist -- {module}\n"
                             f"From the instance audit: these terms are still served in "
                             f"English. Fill in or confirm every msgstr, then merge with "
                             f"i18n_apply.py.")
        written += 1
    return written


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, metavar="DBNAME",
                        help="database to audit (uses psql)")
    parser.add_argument("--lang", default="zh_CN", help="language to check (default zh_CN)")
    parser.add_argument("--module", metavar="A,B", help="restrict to these modules")
    parser.add_argument("--config", metavar="FILE",
                        help="odoo.conf, used to find the module directories whose po files "
                             "tell an already translated term from a missing one")
    parser.add_argument("--root", metavar="DIR", help="source tree, to import Odoo from")
    parser.add_argument("--addons", action="append", default=[], metavar="DIR",
                        help="addons directory (repeatable)")
    parser.add_argument("--overlay-module", action="append", default=[], metavar="DIR",
                        help="the project's translations overlay module (repeatable)")
    parser.add_argument("--out-dir", metavar="DIR",
                        help="write the findings as worklists to DIR/<module>.po")
    parser.add_argument("--summary", action="store_true", help="print the summary")
    parser.add_argument("--list-unserved", type=int, default=0, metavar="N",
                        help="also list up to N terms whose po entry exists but that the "
                             "instance still serves in English, with the po file that "
                             "holds it (0 = summary only, the usual case)")
    parser.add_argument("--with-user-data", action="store_true",
                        help="also report records without an external id (user data, "
                             "which no po file can translate)")
    parser.add_argument("--limit", type=int, default=20000,
                        help="at most N records per field (default 20000)")
    parser.add_argument("--intentional", action="append", default=[], metavar="FILE",
                        help="file of msgids deliberately kept in English, as in "
                             "i18n_scan.py (repeatable); found automatically in "
                             "<addons>/translations/<lang>/_intentional.txt")
    parser.add_argument("--with-module-description", action="store_true",
                        help="also report the long Apps-store description of modules, "
                             "which no language's po translates (summary and short "
                             "description are always reported: the instance renders them "
                             "in the Apps list)")
    args = parser.parse_args()

    modules = set(filter(None, (args.module or "").split(",")))

    roots, _how = resolve_addons_paths(
        Path(args.config).expanduser().resolve() if args.config else None,
        Path(args.root).expanduser().resolve() if args.root else None,
        [Path(raw).expanduser().resolve() for raw in args.addons])
    intentional_files = [Path(raw).expanduser().resolve() for raw in args.intentional] or \
        [root / "translations" / args.lang / INTENTIONAL_FILENAME for root in roots]
    intentional = read_intentional(intentional_files)

    findings = audit(args.db, args.lang, modules, args.with_user_data, args.limit,
                     intentional, args.with_module_description)

    overlay_po = [Path(raw).expanduser().resolve() / "i18n" / f"{args.lang}.po"
                  for raw in args.overlay_module]
    terms, deliveries, kept, pointing = po_index(roots, args.lang, overlay_po,
                                                 installed_modules(args.db))

    for finding in findings:
        key = (finding["occurrence"], finding["term"])
        finding["source"] = deliveries.get(key)
        finding["kept_english_source"] = kept.get(key)
        finding["translated_somewhere"] = finding["term"] in terms
        finding["term_source"] = terms.get(finding["term"], (None, None))[1]
        # A po entry that names this record with a *different* source text.
        # ``deliveries`` cannot see it (the pair does not match), but on a
        # whole-value field the importer keys the row on the xmlid alone, so an
        # upgrade writes that wording even though the text has since changed.
        # Markup is merged node by node, so a stale entry there reaches nothing
        # and the term stays ordinary work.
        finding["stale_source"] = None
        finding["stale_msgid"] = None
        if (finding["kind"] == "standard" and not finding["source"]
                and not finding["kept_english_source"]):
            named = pointing.get(finding["occurrence"])
            if named:
                finding["stale_msgid"] = closest_msgid(named, finding["term"])
                finding["stale_source"] = named[finding["stale_msgid"]]
    # A kept-English entry shadows everything else for this record: what the
    # record needs is a decision, not a translation (`not_served` below is about
    # a wording that exists and has not been imported, which is different).
    addressed = {id(f) for f in findings if f["source"] or f["kept_english_source"]}

    # Nobody translated this text: a fresh translation.
    missing = [f for f in findings
               if id(f) not in addressed and not f["translated_somewhere"]
               and not f["stale_source"]]
    # Translated, but as a term -- no po entry points at this record, so the
    # record stays English.  The overlay needs the occurrence added, and the
    # wording is already in ``terms``.
    unaddressed = [f for f in findings
                   if id(f) not in addressed and f["translated_somewhere"]
                   and not f["stale_source"]]
    # The po entry exists and names this record: nothing to translate, the
    # instance has to import it (upgrade the module that owns the po file) --
    # unless the record is flagged noupdate, where no upgrade ever imports it.
    # A stale entry counts here rather than as work: the upgrade imports it too
    # (on a whole-value field the xmlid is what matches), it just carries the
    # wording of an older revision of the text.
    not_served = [f for f in findings if f["source"] or f["stale_source"]]
    frozen = [f for f in not_served if f["noupdate"]]
    not_served = [f for f in not_served if not f["noupdate"]]
    stale = [f for f in not_served if f["stale_source"]]
    for finding in not_served + frozen:
        finding["waiting_po"] = finding["source"] or finding["stale_source"]
    # The po that names the record ships the English text as its own translation
    # (``msgstr`` == ``msgid``): importing it changes nothing, so the record is
    # English by decision.  Reported so it is not mistaken for a translation
    # nobody has written, and kept out of the worklists.
    kept_english = [f for f in findings
                    if f["kept_english_source"] and not f["source"]]

    fresh = {id(finding) for finding in missing}
    for finding in missing + unaddressed:
        mark = " " if id(finding) in fresh else "+"
        print(f"{mark} {finding['module']:<24} {finding['model']}.{finding['field']:<20} "
              f"{finding['xmlid']:<40} {finding['term'][:60]!r}")
    if args.list_unserved:
        unserved = not_served + frozen + kept_english
        for finding in unserved[:args.list_unserved]:
            source = (finding["source"] or finding["stale_source"]
                      or finding["kept_english_source"])
            mark = ("%" if finding in frozen else
                    "-" if finding["source"] else
                    "~" if finding["stale_source"] else "=")
            print(f"{mark} {finding['module']:<24} {finding['model']}.{finding['field']:<20} "
                  f"{finding['xmlid']:<40} {finding['term'][:45]!r}  "
                  f"<- {source}")
    if args.summary or args.out_dir:
        print(f"\n{len(findings)} English term(s) served by {args.db}:\n"
              f"  {len(missing):>5} translated nowhere yet -- translate these\n"
              f"  {len(unaddressed):>5} translated somewhere, but no po entry points at "
              f"the record (marked '+') -- the overlay adds the occurrence\n"
              f"  {len(not_served):>5} already in a po that names the record -- not "
              f"translation work, the instance has not imported it\n"
              f"  {len(frozen):>5} the record is noupdate, so no po entry reaches it "
              f"(marked '%') -- set the value in the UI or with a script\n"
              f"  {len(kept_english):>5} the po itself ships the English text "
              f"(msgstr == msgid, marked '=') -- a decision to review, not a "
              f"translation", file=sys.stderr)
        by_module: dict[str, int] = {}
        for finding in missing + unaddressed:
            by_module[finding["module"]] = by_module.get(finding["module"], 0) + 1
        if by_module:
            print("  work to do, by module:", file=sys.stderr)
            for module, count in sorted(by_module.items(), key=lambda item: -item[1]):
                print(f"    {module:<30} {count:>6}", file=sys.stderr)
        for label, group, key_name in (
                ("waiting in these po files, upgrade their module to import them:",
                 not_served, "waiting_po"),
                ("noupdate records the po cannot reach, set them in the UI:",
                 frozen, "waiting_po"),
                ("kept in English by these po files, review the decision:",
                 kept_english, "kept_english_source")):
            if not group:
                continue
            by_source: dict[str, int] = {}
            for finding in group:
                by_source[finding[key_name]] = by_source.get(finding[key_name], 0) + 1
            print(f"  {label}", file=sys.stderr)
            for source, count in sorted(by_source.items(), key=lambda item: -item[1])[:12]:
                print(f"    {source:<30} {count:>6}", file=sys.stderr)
        if stale:
            print(f"  note: {len(stale)} of those entries name the record with a source "
                  f"text older than the record's value (marked '~').  A whole-value field "
                  f"is imported on its xmlid alone, so the upgrade writes that wording "
                  f"even though the text changed since: check it against the current "
                  f"text, and correct it through the overlay when it no longer fits.",
                  file=sys.stderr)
        other = translatable_other_types(args.db)
        if other:
            print(f"  note: {len(other)} translatable field(s) are of a type this audit "
                  f"does not read (not char/text/html): {', '.join(other[:12])}"
                  f"{', ...' if len(other) > 12 else ''}", file=sys.stderr)
    if args.out_dir:
        work = [f for f in missing + unaddressed if f["module"] != "?"]
        if len(work) != len(missing) + len(unaddressed):
            # No external id means no occurrence a po entry could carry, so
            # these records cannot be translated through a po file at all
            # (--with-user-data is what brings them in).  Report, do not write.
            print(f"  {len(missing) + len(unaddressed) - len(work)} record(s) without an "
                  f"external id are not written to a worklist: no po entry can name "
                  f"them", file=sys.stderr)
        written = write_worklists(Path(args.out_dir).expanduser().resolve(), args.lang,
                                  work,
                                  {term: msgstr for term, (msgstr, _src) in terms.items()})
        print(f"wrote {written} worklist file(s) to {args.out_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
