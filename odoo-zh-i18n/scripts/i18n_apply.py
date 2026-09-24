#!/usr/bin/env python3
"""Check a translation worklist and build it into the translations overlay module.

Second half of the workflow started by ``i18n_scan.py``: fill in the ``msgstr``
of the worklist entries, then build them into the po file of a project-owned
overlay module -- never into the upstream addons tree, which an upstream
synchronisation (``git reset --hard``) replaces.

    scripts/i18n_apply.py check translations/zh_CN
    scripts/i18n_apply.py build translations/zh_CN --module-dir <addons>/sn_odoo20_translations --write

Odoo imports the ``model:`` / ``model_terms:`` entries of that file into the
database when the overlay module is installed or upgraded, and the module merges
its ``code:`` entries into the runtime translation cache on load; see
``references/odoo-i18n-mechanics.md``.  Worklists are cumulative, so after an
upstream pull the overlay is rebuilt from them with the same command.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import polib

from i18n_common import format_entry, module_of, po_header, read_intentional

GENERATOR = "i18n_apply.py (odoo-zh-i18n)"
WORKLIST_TITLE = ("Translations overlay built from the project worklists.\n"
                  "Do not edit by hand: translate the worklists and rebuild.")

# printf-style placeholders: %s, %d, %(name)s, %(amount).2f, %%
# The space flag is left out on purpose: Odoo never uses it, while a plain
# percent followed by a word ("CA State 6% Exempt") would otherwise be read as
# the placeholder "% E" and every translation of such a label would be
# reported as dropping it.
PLACEHOLDER_RE = re.compile(
    r"%(?:\((?P<name>[^)]*)\))?[-#0+]*\d*(?:\.\d+)?[diouxXeEfFgGcrsa%]")
TAG_RE = re.compile(r"</?([A-Za-z][A-Za-z0-9]*)(?=[\s/>])")
ENTITY_RE = re.compile(r"&(?:[a-zA-Z]+|#\d+|#x[0-9a-fA-F]+);")
# Han characters, plus the fullwidth forms a Chinese localisation drags along:
# "(" -> "（" and ":" -> "：" are real translations even though they hold no
# Han character, and warning about them buries the entries that really are
# still English.  The word-separating space U+3000 is part of that group too.
LOCALIZED_RE = re.compile(
    r"[\u3000-\u9fff\uf900-\ufaff\uff01-\uff60\uffe0-\uffe6]")


def collect_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            files.extend(sorted(path.glob("*.po")))
        elif path.is_file():
            files.append(path)
        else:
            sys.exit(f"no such file or directory: {path}")
    if not files:
        sys.exit("no .po worklist files found")
    return files


def read_worklist(files: list[Path]) -> list[polib.POEntry]:
    """Every entry of every worklist file, filled in or not."""
    entries = []
    for path in files:
        for entry in polib.pofile(str(path), encoding="utf-8"):
            if entry.msgid and not entry.obsolete:
                entry.worklist = str(path)  # type: ignore[attr-defined]
                entries.append(entry)
    return entries


def placeholders(text: str) -> list[str]:
    return [match.group(0) for match in PLACEHOLDER_RE.finditer(text) if match.group(0) != "%%"]


def compare(entry: polib.POEntry) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for one filled worklist entry."""
    msgid, msgstr = entry.msgid, entry.msgstr
    errors: list[str] = []
    warnings: list[str] = []

    source, target = Counter(placeholders(msgid)), Counter(placeholders(msgstr))
    if dropped := sorted(source - target):
        errors.append(f"placeholder(s) missing: {', '.join(dropped)}")
    if added := sorted(target - source):
        errors.append(f"placeholder(s) not in the source string: {', '.join(added)}")
    source_tags = Counter(tag.lower() for tag in TAG_RE.findall(msgid))
    target_tags = Counter(tag.lower() for tag in TAG_RE.findall(msgstr))
    if source_tags != target_tags:
        errors.append(f"markup tags differ: {sorted(source_tags)} -> {sorted(target_tags)}")
    if Counter(ENTITY_RE.findall(msgid)) != Counter(ENTITY_RE.findall(msgstr)):
        warnings.append("HTML entities differ from the source string")

    if msgstr == msgid:
        warnings.append("translation is identical to the source string")
    elif not LOCALIZED_RE.search(msgstr):
        warnings.append("translation contains no Chinese characters")
    if msgid.count("\n") != msgstr.count("\n"):
        warnings.append(f"line breaks differ: {msgid.count(chr(10))} -> {msgstr.count(chr(10))}")
    if msgid.strip() != msgid and msgstr != msgid:
        leading = re.match(r"\s*", msgid).group(0)
        trailing = re.search(r"\s*$", msgid).group(0)
        if not msgstr.startswith(leading) or not msgstr.endswith(trailing):
            warnings.append("leading/trailing whitespace differs")
    return errors, warnings


def report(entry: polib.POEntry, errors: list[str], warnings: list[str]) -> None:
    where = f"{Path(getattr(entry, 'worklist', '?')).name}:{module_of(entry) or '?'}: {entry.msgid[:60]!r}"
    for message in errors:
        print(f"ERROR {where}: {message}", file=sys.stderr)
    for message in warnings:
        print(f"WARNING {where}: {message}", file=sys.stderr)


def read_baseline(raw: str) -> dict[str, str]:
    """The msgid -> msgstr of an already accepted state, for ``check --baseline``.

    Accepts a po file, a directory of them (the overlay module's own
    ``i18n/zh_CN.po`` is the usual choice), or ``<git-ref>:<path>``, which is
    read with ``git show`` so a ref from before the current work can be used
    without checking it out.
    """
    path = Path(raw).expanduser()
    if path.exists():
        entries = read_worklist(collect_files([str(path)]))
    elif ":" in raw:
        proc = subprocess.run(["git", "show", raw], capture_output=True, text=True)
        if proc.returncode != 0:
            sys.exit(f"{raw}: not a file, a directory or something git can show:\n"
                     f"{proc.stderr.strip()}")
        entries = list(polib.pofile(proc.stdout, encoding="utf-8"))
    else:
        sys.exit(f"no such file or directory: {path} "
                 f"(or use <git-ref>:<path> to read one out of git)")
    baseline = {entry.msgid: entry.msgstr for entry in entries
                if entry.msgid and entry.msgstr.strip()}
    if not baseline:
        sys.exit(f"{raw}: the baseline holds no translated entry")
    return baseline


def check(entries: list[polib.POEntry], strict: bool,
          baseline: dict[str, str] | None = None) -> tuple[int, int]:
    """Report on the filled entries; return (problems, messages suppressed).

    A baseline is the state that was already reviewed and accepted.  Worklists
    are cumulative, so without one every run reprints the long-standing
    warnings (labels that stay in English on purpose, "%s:" -> "%s：" and the
    like) and nobody can see the messages this run introduced.  An entry counts
    as old only when its msgstr is byte-identical to the accepted one.
    """
    problems = 0
    suppressed = 0
    for entry in entries:
        errors, warnings = compare(entry)
        if strict and warnings:
            errors, warnings = errors + warnings, []
        if not errors and not warnings:
            continue
        if baseline is not None and baseline.get(entry.msgid) == entry.msgstr:
            suppressed += len(errors) + len(warnings)
            continue
        problems += len(errors)
        report(entry, errors, warnings)
    return problems, suppressed


def merge_group(group: list[polib.POEntry]) -> polib.POEntry:
    """One po entry per msgid, with the comments/occurrences of the whole group.

    A po file cannot hold the same msgid twice, and every occurrence of an entry
    shares its translation, so entries that agree are merged into a single one
    that keeps all of their references.
    """
    comments: list[str] = []
    occurrences: list[tuple[str, str]] = []
    for entry in group:
        for line in (entry.comment or "").split("\n"):
            if line and line not in comments:
                comments.append(line)
        for occurrence in entry.occurrences:
            if tuple(occurrence) not in occurrences:
                occurrences.append(tuple(occurrence))
    return polib.POEntry(
        msgid=group[0].msgid,
        msgstr=group[0].msgstr,
        comment="\n".join(comments),
        occurrences=occurrences,
    )


def intentional_dirs(paths: list[str]) -> list[Path]:
    """Directories that may hold an _intentional.txt, for the given paths."""
    return [Path(raw).expanduser() if Path(raw).is_dir() else Path(raw).parent
            for raw in paths]


def report_intentional(files: list[Path], entries: list[polib.POEntry]) -> tuple[int, int]:
    """Cross-check the deliberate omissions against the worklists.

    Returns (acknowledged, problems).  A term that is listed as intentional but
    carries a translation is a contradiction: the list says "keep this English"
    while the worklist ships a translation of it.  Absence from the worklists is
    the normal state -- the scan keeps declared terms out of them -- so whether a
    declaration is still needed is answered by ``i18n_scan.py``, which reads the
    source, not here.
    """
    declared = read_intentional(intentional_dirs([str(path) for path in files]))
    if not declared:
        return 0, 0
    by_msgid = {entry.msgid: entry for entry in entries}
    problems = 0
    for msgid, reason in sorted(declared.items()):
        entry = by_msgid.get(msgid)
        where = f"{getattr(entry, 'worklist', '?').split('/')[-1] if entry else '?'}: {msgid[:60]!r}"
        if entry is not None and entry.msgstr.strip():
            print(f"WARNING _intentional.txt: {where}: is translated, drop it from the list",
                  file=sys.stderr)
            problems += 1
        elif entry is not None and not reason:
            print(f"WARNING _intentional.txt: {where}: no '# reason' given", file=sys.stderr)
    return len(declared), problems


def cmd_check(args) -> int:
    files = collect_files(args.paths)
    entries = read_worklist(files)
    filled = [entry for entry in entries if entry.msgstr.strip()]
    baseline = read_baseline(args.baseline) if args.baseline else None
    problems, suppressed = check(filled, args.strict, baseline)
    intentional, stale = report_intentional(files, entries)
    if stale and args.strict:
        problems += stale
    summary = [f"{len(files)} file(s), {len(filled)}/{len(entries)} entries translated",
               f"{intentional} intentionally kept in English",
               f"{problems} error(s)"]
    if baseline is not None:
        summary.append(f"{suppressed} message(s) already accepted by the baseline")
    print(", ".join(summary))
    return 1 if problems else 0


def cmd_build(args) -> int:
    files = collect_files(args.paths)
    entries = read_worklist(files)
    filled = [entry for entry in entries if entry.msgstr.strip()]
    if not filled:
        print(f"nothing to build: {len(entries)} entries, none translated yet")
        return 0
    problems, _ = check(filled, args.strict)
    if problems:
        print(f"{problems} error(s); fix them or blank out the msgstr of those entries",
              file=sys.stderr)
        return 1
    if any(not module_of(entry) for entry in filled):
        sys.exit("every worklist entry needs a '#. module: <name>' comment")

    module_dir = Path(args.module_dir).expanduser().resolve()
    if not (module_dir / "__manifest__.py").is_file():
        sys.exit(f"{module_dir} is not an Odoo module (no __manifest__.py)")

    groups: dict[str, list[polib.POEntry]] = {}
    for entry in filled:
        groups.setdefault(entry.msgid, []).append(entry)
    conflicts: dict[str, dict[str, list[str]]] = {}
    for msgid, group in groups.items():
        variants: dict[str, list[str]] = {}
        for entry in group:
            variants.setdefault(entry.msgstr, []).append(Path(entry.worklist).name)
        if len(variants) > 1:
            conflicts[msgid] = variants
    if conflicts:
        for msgid, variants in sorted(conflicts.items())[:10]:
            # Which worklist each variant came from: the two files disagree and
            # one of them has to give way, so name them.
            detail = " | ".join(f"{text!r} ({', '.join(sorted(set(where)))})"
                                for text, where in sorted(variants.items()))
            print(f"CONFLICT {msgid[:60]!r}: {detail}", file=sys.stderr)
        sys.exit(f"{len(conflicts)} msgid(s) are translated in more than one way; "
                 f"a single po file can only hold one translation per msgid, so "
                 f"unify them in the worklists first")

    merged = sorted((merge_group(group) for group in groups.values()),
                    key=lambda entry: entry.msgid)
    target = module_dir / "i18n" / f"{args.lang}.po"
    if not args.write:
        print(f"would write {len(merged)} term(s) ({len(filled)} worklist entries) "
              f"into {target}")
        print("dry run; re-run with --write to modify the file")
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    blocks = [po_header(args.lang, GENERATOR, WORKLIST_TITLE)]
    blocks += [format_entry(entry) for entry in merged]
    target.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")

    written = [entry for entry in polib.pofile(str(target), encoding="utf-8") if entry.msgid]
    if len(written) != len(merged) or \
            {entry.msgid: entry.msgstr for entry in written} != \
            {entry.msgid: entry.msgstr for entry in merged}:
        sys.exit(f"{target}: the file does not read back as written")
    print(f"wrote {len(merged)} term(s) into {target}")
    print("\nNext steps:")
    print("  * data terms (model / model_terms) are imported when the module is")
    print("    installed or upgraded:  odoo-bin -d <db> -u <module> --stop-after-init")
    print("  * code terms are merged at load time, so the service must be restarted")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser(
        "check", help="report translation errors and warnings")
    check_parser.add_argument("paths", nargs="+", metavar="PATH",
                              help="worklist .po file(s), or a directory of them")
    check_parser.add_argument("--strict", action="store_true",
                              help="treat warnings as errors")
    check_parser.add_argument("--baseline", metavar="PO|REF:PATH",
                              help="accepted state, as a po file, a directory of "
                                   "them or <git-ref>:<path> (e.g. the overlay "
                                   "module's own i18n/zh_CN.po); entries whose "
                                   "msgstr is unchanged from it are not reported, "
                                   "so only the messages of this round show up")
    check_parser.set_defaults(func=cmd_check)

    build = subparsers.add_parser(
        "build", help="build the overlay module's po file from the worklists")
    build.add_argument("paths", nargs="+", metavar="PATH",
                       help="worklist .po file(s), or a directory of them")
    build.add_argument("--module-dir", required=True, metavar="DIR",
                       help="directory of the overlay module that carries the translations")
    build.add_argument("--lang", default="zh_CN", help="target language (default zh_CN)")
    build.add_argument("--write", action="store_true", help="actually write the file")
    build.add_argument("--strict", action="store_true",
                       help="treat warnings as errors")
    build.set_defaults(func=cmd_build)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
