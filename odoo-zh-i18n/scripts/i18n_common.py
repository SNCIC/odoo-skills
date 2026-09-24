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
import sys
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
# HTML view/mail terms are user-visible text like any other: leaving them out of
# the default would make the documented scan under-report by hundreds of terms.
DEFAULT_CATEGORIES = (CODE, FIELD, TERMS, TERMS_HTML)

CJK_RE = re.compile(r"[\u3000-\u9fff\uf900-\ufaff]")
LETTER_RE = re.compile(r"[A-Za-z]")
MARKUP_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9]*")
# The long Apps-store blurb of a module.  No language translates it -- every
# upstream po ships the entry with an empty msgstr -- so it has a category of
# its own, kept out of the worklists unless the caller asks for it.  The
# *short* description and the summary are not in this class: the instance
# imports them from the po entry that names ``base.module_<name>`` and shows
# them in the Apps list, so they are ordinary one-line terms.
MODULE_DESCRIPTION_RE = re.compile(r"model:ir\.module\.module,description:")
MODULE_COMMENT_RE = re.compile(r"^module[s]?:\s*(\w+)\s*$")
# Printer control code: ZPL (the label templates of product/stock keep the raw
# ZPL string as a text node) and ESC/POS.  They are layout commands for the
# label printer, never text a user reads, so they can never be translated.
CONTROL_CODE_RE = re.compile(r"^(?:\^[A-Z0-9]{2}|~[A-Z]{2})|\x1b")


INTENTIONAL_FILENAME = "_intentional.txt"


def read_intentional(paths: list[Path]) -> dict[str, str]:
    r"""Terms a project deliberately keeps in English, by msgid -> reason.

    Brand names, paper sizes, printer control codes and single-letter keyboard
    shortcuts have no translation in any language.  Listing them once (one msgid
    per line, an optional ``# reason`` after it, ``#`` also starts a full-line
    comment) keeps them out of the scanned worklists, so after an upstream pull
    the coverage report is still a list of genuine work instead of a list of
    decisions already taken.  Each ``path`` is either such a file or a directory
    holding a default-named ``_intentional.txt``.

    A msgid may itself contain ``#`` -- ``iPhone #1``, ``# of Blocks`` -- and the
    plain ``partition("#")`` would cut the declaration in the middle of the term
    (the term would never match anything).  ``\#`` declares such a term; only an
    unescaped ``#`` starts the reason.
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
            head, separator, reason = split_declaration(line)
            msgid = unescape_msgid(head.strip())
            found[msgid] = reason.strip() if separator else ""
    return found


def split_declaration(line: str) -> tuple[str, str, str]:
    r"""Split one ``msgid [# reason]`` line on the first *unescaped* ``#``.

    The head keeps its ``\#`` so ``unescape_msgid`` can see the escape; callers
    turn it into a literal ``#`` afterwards.  Returns ``(head, separator,
    reason)``, the separator being empty when the line declares no reason.
    """
    match = re.search(r"(?<!\\)#", line)
    if match is None:
        return line, "", ""
    return line[:match.start()], "#", line[match.end():]


def unescape_msgid(text: str) -> str:
    """Turn the ``\\n`` / ``\\t`` / ``\\\\`` escapes of a declaration into characters.

    A view-arch term is usually several lines long (``<span>`` per line), and
    ``_intentional.txt`` is read line by line, so the newlines have to be
    written as escapes there.  Anything else is taken literally.
    """
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            following = text[index + 1]
            if following in "nt\\#":
                out.append({"n": "\n", "t": "\t", "#": "#"}.get(following, "\\"))
                index += 2
                continue
        out.append(char)
        index += 1
    return "".join(out)


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


def exposes_odoo_package(directory: Path) -> bool:
    """Whether ``directory`` can go on ``sys.path`` to make ``import odoo`` work.

    Three things have to hold, and each one has bitten a caller already: the
    directory must *contain* the package (adding the package directory itself
    shadows the ``logging`` and ``http`` modules Odoo imports, and the import
    then dies with a circular-import error that names neither), the package must
    be there at all, and it must be recognised as Odoo -- ``release.py`` is the
    marker rather than ``__init__.py`` because 20.0 renames that file to
    ``init.py`` and relies on the directory being a namespace package.
    """
    package = directory / "odoo"
    return (package / "release.py").is_file() and (package / "tools").is_dir()


def looks_like_addons_dir(path: Path) -> bool:
    """Whether ``path`` holds at least one module."""
    if not path.is_dir():
        return False
    return any((child / "__manifest__.py").is_file()
               for child in path.iterdir() if child.is_dir())


def resolve_addons_paths(conf: Path | None = None, root: Path | None = None,
                         extra: list[Path] = ()) -> tuple[list[Path], str]:
    """Every addons directory the project's Odoo loads, and how they were found.

    Odoo loads the ``addons_path`` of its configuration file *and* its own
    ``odoo/addons`` package directory, which no configuration file mentions.
    That directory is where ``base`` lives, so a scan that only knows the
    configured paths silently reports "nothing to translate" for the core
    modules -- the biggest blind spot of a po-based scan.

    Importing Odoo is the only exact answer; parsing the configuration file and
    its conventional siblings is the fallback for a python that cannot import
    the source tree.  Returns the directories plus a short label of the source
    (``odoo``, ``config`` or ``none``) so the caller can report which one it
    used.
    """
    candidates: list[Path] = []

    # ``root`` is documented as the project directory (which holds an ``odoo``
    # source tree) *or* that source tree itself, so both are candidates;
    # ``exposes_odoo_package`` keeps the one that must not go on sys.path off
    # it.
    if root:
        for candidate in (root, root / "odoo"):
            if exposes_odoo_package(candidate) and str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
    try:
        from odoo.modules import module as odoo_module
        from odoo.tools import config as odoo_config

        if conf and conf.is_file():
            odoo_config.parse_config(["-c", str(conf)])
        odoo_module.initialize_sys_path()
        import odoo.addons

        found = [Path(directory) for directory in getattr(odoo.addons, "__path__", [])]
        if found and any(looks_like_addons_dir(directory) for directory in found):
            return found, "odoo"
    except BaseException:  # noqa: BLE001 - any failure means "fall back"
        pass

    if conf and conf.is_file():
        import configparser

        parser = configparser.RawConfigParser()
        try:
            parser.read(str(conf))
            raw = parser.get("options", "addons_path", fallback="")
        except configparser.Error:
            raw = ""
        for field in raw.split(","):
            if field.strip():
                candidates.append(Path(field.strip()))

    resolved: list[Path] = []
    for directory in candidates + list(extra):
        siblings = [
            directory,
            directory / "odoo" / "addons",
            directory.parent / "odoo" / "addons",
            directory.parent / "addons",
        ]
        for sibling in siblings:
            if looks_like_addons_dir(sibling) and sibling not in resolved:
                resolved.append(sibling)
    for directory in extra:
        if looks_like_addons_dir(directory) and directory not in resolved:
            resolved.append(directory)
    return resolved, "config" if resolved else "none"
