#!/usr/bin/env python3
"""Shared helpers for the Odoo translation scripts.

Kept dependency-light on purpose: polib plus the standard library, nothing
that needs an Odoo environment.  The one Odoo rule worth spelling out here is
how a language maps to files -- ``odoo.tools.translate.get_po_paths`` reads
``i18n/<base>.po``, ``i18n_extra/<base>.po``, ``i18n/<lang>.po`` and finally
``i18n_extra/<lang>.po``, so for zh_CN a ``zh.po`` is read first and
``i18n_extra/zh_CN.po`` wins over everything else.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import polib

# Categories, ordered by how visible the terms are to end users.
CODE = "code"                    # python _(), JS _t(), QWeb templates
FIELD = "field"                  # model field labels, selections, help texts
TERMS = "terms"                  # text of XML data records / views
TERMS_HTML = "terms_html"        # same, but containing markup
DESCRIPTION = "description"      # module long description in the Apps store
OTHER = "other"
ALL_CATEGORIES = (CODE, FIELD, TERMS, TERMS_HTML, DESCRIPTION, OTHER)
DEFAULT_CATEGORIES = (CODE, FIELD, TERMS)

CJK_RE = re.compile(r"[\u3000-\u9fff\uf900-\ufaff]")
LETTER_RE = re.compile(r"[A-Za-z]")
MARKUP_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9]*")
MODULE_DESCRIPTION_RE = re.compile(r"model:ir\.module\.module,description:")
MODULE_COMMENT_RE = re.compile(r"^module[s]?:\s*(\w+)\s*$")
# Printer control code: ZPL (the label templates of product/stock keep the raw
# ZPL string as a text node) and ESC/POS.  They are layout commands for the
# label printer, never text a user reads, so they can never be translated.
CONTROL_CODE_RE = re.compile(r"^(?:\^[A-Z0-9]{2}|~[A-Z]{2})|\x1b")


INTENTIONAL_FILENAME = "_intentional.txt"


def read_intentional(paths: list[Path]) -> dict[str, str]:
    """Terms a project deliberately keeps in English, by msgid -> reason.

    Brand names, paper sizes, printer control codes and single-letter keyboard
    shortcuts have no translation in any language.  Listing them once (one msgid
    per line, an optional ``# reason`` after it, ``#`` also starts a full-line
    comment) keeps them out of the scanned worklists, so after an upstream pull
    the coverage report is still a list of genuine work instead of a list of
    decisions already taken.  Each ``path`` is either such a file or a directory
    holding a default-named ``_intentional.txt``.
    """
    found: dict[str, str] = {}
    for raw in paths:
        path = raw / INTENTIONAL_FILENAME if raw.is_dir() else raw
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            msgid, _, reason = line.partition("#")
            found[msgid.strip()] = reason.strip()
    return found


def base_langs(lang: str) -> list[str]:
    """Language files Odoo reads for ``lang``, lowest precedence first."""
    base = lang.split("_", 1)[0]
    langs = [base]
    if base == "es" and lang not in ("es_ES", "es_419"):
        langs.append("es_419")
    if lang == "zh_HK":
        langs.append("zh_TW")
    if lang != base:
        langs.append(lang)
    return langs


def po_candidates(module_dir: Path, lang: str) -> list[Path]:
    return [
        module_dir / sub / f"{code}.po"
        for code in base_langs(lang)
        for sub in ("i18n", "i18n_extra")
    ]


def find_pot(module_dir: Path, module: str) -> Path | None:
    """The term template Odoo's tooling extracted from the module source."""
    preferred = module_dir / "i18n" / f"{module}.pot"
    if preferred.is_file():
        return preferred
    for sub in ("i18n", "i18n_extra"):
        pots = sorted((module_dir / sub).glob("*.pot"))
        if len(pots) == 1:
            return pots[0]
    return None


def read_translations(module_dir: Path, lang: str) -> dict[str, str]:
    """Non-empty translations of this module, later files overriding earlier."""
    translations: dict[str, str] = {}
    for path in po_candidates(module_dir, lang):
        if not path.is_file():
            continue
        for entry in polib.pofile(str(path), encoding="utf-8"):
            # Fuzzy entries are used at runtime as well: Odoo's po reader takes
            # the msgstr as it is, so only an empty one means "untranslated".
            if entry.msgid and not entry.obsolete and entry.msgstr.strip():
                translations[entry.msgid] = entry.msgstr
    return translations


def iter_modules(roots: list[Path], only: set[str] = frozenset(), exclude: set[str] = frozenset()):
    """Yield (name, path) for every addon below ``roots``; shallowest path wins."""
    found: dict[str, Path] = {}
    for root in roots:
        if not root.is_dir():
            raise SystemExit(f"addons directory not found: {root}")
        for manifest in root.rglob("__manifest__.py"):
            path = manifest.parent
            if path.name in found and len(found[path.name].parts) <= len(path.parts):
                continue
            found[path.name] = path
    for name in sorted(found):
        if only and name not in only:
            continue
        if name in exclude:
            continue
        yield name, found[name]


def categorize(msgid: str, occurrences: list[list[str]]) -> str:
    kinds = {occ[0].split(":", 1)[0] for occ in occurrences if occ[0]}
    first = occurrences[0][0] if occurrences else ""
    if CODE in kinds:
        return CODE
    if MODULE_DESCRIPTION_RE.match(first):
        return DESCRIPTION
    if "model_terms" in kinds:
        return TERMS_HTML if MARKUP_RE.search(msgid) else TERMS
    if "model" in kinds:
        return FIELD
    return OTHER


def declared_modules(entry: polib.POEntry) -> list[str]:
    """Every module a worklist/po entry names in its `#. module:` comments.

    Odoo's own po writer emits one line per module an entry is shared by, and
    its reader keeps only the first (``PoFileReader.__iter__``: "in case of
    moduleS keep only the first"), which is not enough to know which po files
    the term belongs to.
    """
    modules: list[str] = []
    for line in (entry.comment or "").split("\n"):
        match = MODULE_COMMENT_RE.match(line.strip())
        if match and match.group(1) not in modules:
            modules.append(match.group(1))
    return modules


def module_of(entry: polib.POEntry) -> str | None:
    """The module a worklist/po entry belongs to, from its `#. module:` comment."""
    if modules := declared_modules(entry):
        return modules[0]
    for reference, _lineno in entry.occurrences:
        if reference.startswith("code:addons/"):
            return reference.split("/", 2)[2].split("/", 1)[0]
    return None


def format_entry(entry: dict | polib.POEntry, wrap_width: int = 78) -> str:
    """Render a complete .po block: comments, occurrences, msgid, msgstr."""
    if isinstance(entry, polib.POEntry):
        po_entry = entry
    else:
        po_entry = polib.POEntry(
            msgid=entry["msgid"],
            msgstr=entry.get("translation", ""),
            comment="\n".join(entry.get("comments") or []),
            occurrences=[tuple(occ) for occ in entry.get("occurrences") or []],
        )
    return polib.POEntry.__unicode__(po_entry, wrap_width).rstrip("\n")


def entry_target_modules(entry: polib.POEntry) -> set[str]:
    """The modules an overlay po entry applies to.

    Two things can be said about an entry: which occurrences carry it -- the
    xmlid's module for data (``model:...,field:<module>.<xmlid>``, the module
    Odoo's importer writes the translation for), the source file for code
    (``code:addons/<module>/...``, the module the runtime resolves it for) --
    and which po file it came from, the ``#. module:`` comment.  Both are kept:
    the summary of ``account_payment`` is imported for the ``base`` record
    ``base.module_account_payment``, yet it is the po of ``account_payment``
    that has to hold it, and calling the term translated while only one of the
    two modules is covered would hide real work from the scan.
    """
    modules: set[str] = set()
    for reference, _lineno in entry.occurrences:
        if reference.startswith("code:"):
            parts = reference[len("code:"):].split("/")
            if len(parts) > 1 and parts[0] == "addons":
                modules.add(parts[1])
            continue
        match = re.match(r"(?:model|model_terms):[\w.]+,(\w+):(\w+)\.", reference)
        if match:
            modules.add(match.group(2))
    modules.update(declared_modules(entry))
    if not modules and (fallback := module_of(entry)):
        modules.add(fallback)
    return modules


def read_overlay(path: Path) -> dict[str, dict[str, str]]:
    """``{module: {msgid: msgstr}}`` for the overlay po at ``path``."""
    overlay: dict[str, dict[str, str]] = {}
    if not path.is_file():
        return overlay
    for entry in polib.pofile(str(path), encoding="utf-8"):
        if not entry.msgid or entry.obsolete or not entry.msgstr.strip():
            continue
        for module in entry_target_modules(entry):
            overlay.setdefault(module, {})[entry.msgid] = entry.msgstr
    return overlay


def po_header(lang: str, generator: str, title: str | None = None) -> str:
    """A valid .po header for a language, optionally with a title comment."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M%z")
    plural = "nplurals=1; plural=0;" if lang.startswith("zh") else "nplurals=2; plural=(n != 1);"
    header = (
        'msgid ""\n'
        'msgstr ""\n'
        '"Project-Id-Version: Odoo Server\\n"\n'
        '"Report-Msgid-Bugs-To: \\n"\n'
        f'"POT-Creation-Date: {now}\\n"\n'
        f'"PO-Revision-Date: {now}\\n"\n'
        '"Last-Translator: \\n"\n'
        '"Language-Team: \\n"\n'
        f'"Language: {lang}\\n"\n'
        '"MIME-Version: 1.0\\n"\n'
        '"Content-Type: text/plain; charset=UTF-8\\n"\n'
        '"Content-Transfer-Encoding: \\n"\n'
        f'"Plural-Forms: {plural}\\n"\n'
        f'"X-Generator: {generator}\\n"'
    )
    if title:
        header = "".join(f"# {line}\n" for line in title.split("\n")) + "\n" + header
    return header
