"""Merge this module's code translations into Odoo's runtime translation cache.

Odoo resolves a code string from the po file that sits next to the module
*declaring* it: ``odoo.tools.translate.CodeTranslations`` reads
``<module>/i18n/<lang>.po`` and ``<module>/i18n_extra/<lang>.po`` through
``get_po_paths()``.  A module can therefore never translate another module's
python ``_()``, JS ``_t()`` or QWeb strings through files alone.

What it *can* do is contribute to ``odoo.tools.translate.code_translations``,
the process-wide cache those lookups go through.  This module merges its own
po entries into that cache when it is loaded: the official translations stay,
ours win on conflicts, and the result keeps the exact structure Odoo expects
(``frozendict`` of ``{src: value}`` for python, a ``messages`` tuple for JS).

Nothing here touches upstream source files, so synchronising the Odoo tree
cannot break the translations.  Every step is defensive: if a future release
changes the cache, the module logs a warning and the data terms -- which Odoo
imports itself -- keep working.
"""

import logging
import re
from pathlib import Path

import polib

from odoo.tools.misc import frozendict
from odoo.tools.translate import (
    JAVASCRIPT_TRANSLATION_COMMENT,
    PYTHON_TRANSLATION_COMMENT,
    code_translations,
)

_logger = logging.getLogger(__name__)

MODULE_DIR = Path(__file__).resolve().parent
MODULE_NAME = MODULE_DIR.name          # the addon directory name Odoo uses
I18N_DIR = MODULE_DIR / 'i18n'
_applied = False
_CODE_MARKERS = {
    'python': PYTHON_TRANSLATION_COMMENT,
    'javascript': JAVASCRIPT_TRANSLATION_COMMENT,
}


def _code_targets(entry):
    """``{(kind, module)}`` the code terms of one entry belong to.

    Both parts come from the occurrence path: a ``.py`` file declares a python
    term, anything else (JS, QWeb template, spreadsheet dashboard) a javascript
    one, which is what the ``#. odoo-python`` / ``#. odoo-javascript`` comment
    says as well, and the two must agree for the entry to be kept.

    The module cannot be read from the ``#. module:`` comment: Odoo's own po
    reader keeps only the FIRST of those lines (``PoFileReader.__iter__``, "in
    case of moduleS keep only the first"), and this file is the merged worklist
    of every module, so one entry can carry the code occurrences of several of
    them.  Filing such a term under a single module would leave the bundles of
    all the others in English.
    """
    comments = entry.comment or ''
    marked = {kind for kind, marker in _CODE_MARKERS.items() if marker in comments}
    declared = re.findall(r'^module:\s*(\w+)\s*$', comments, re.M)
    targets = set()
    for reference, _lineno in entry.occurrences:
        if not reference.startswith('code:'):
            continue
        parts = reference[len('code:'):].split('/')
        kind = 'python' if parts[-1].split(':', 1)[0].endswith('.py') else 'javascript'
        if kind not in marked:
            continue
        if len(parts) >= 3 and parts[0] == 'addons':
            targets.add((kind, parts[1]))
        else:
            # a path without its module: `base` ships `code:addons/models.py`
            targets.update((kind, module) for module in declared)
    return targets


def _read_po(path):
    """Return ``{(kind, target_module): {source: translation}}`` for one po file."""
    entries = {}
    for entry in polib.pofile(str(path), encoding='utf-8'):
        if entry.obsolete or not entry.msgid or not entry.msgstr:
            continue
        targets = _code_targets(entry)
        if not targets:
            if any(reference.startswith('code:') for reference, _ in entry.occurrences):
                _logger.warning(
                    "%s: ignoring %r, its '#. %s' / '#. %s' comment and its "
                    "occurrence paths disagree", path.name, entry.msgid,
                    PYTHON_TRANSLATION_COMMENT, JAVASCRIPT_TRANSLATION_COMMENT)
            continue
        for target in targets:
            entries.setdefault(target, {})[entry.msgid] = entry.msgstr
    return entries


def _merge_python(key, translations):
    merged = dict(code_translations.get_python_translations(*key))
    merged.update(translations)
    code_translations.python_translations[key] = frozendict(merged)
    return len(translations)


def _merge_javascript(key, translations):
    messages = code_translations.get_web_translations(*key)['messages']
    merged = {message['id']: message['string'] for message in messages}
    merged.update(translations)
    code_translations.web_translations[key] = frozendict({
        'messages': tuple(
            frozendict({'id': source, 'string': value})
            for source, value in sorted(merged.items())
        ),
    })
    return len(translations)


def install():
    """Merge every po file of this module into the code translation cache.

    Called while the module is imported and again from the manifest's
    ``post_load``; the merge is idempotent, so only the first call does work.
    """
    global _applied
    if _applied:
        return
    if not hasattr(code_translations, 'python_translations') or \
            not hasattr(code_translations, 'web_translations'):
        _logger.warning(
            f"{MODULE_NAME}: odoo.tools.translate.code_translations no "
            "longer has python_translations/web_translations; code translations "
            "are left untouched (data translations still work)")
        return
    try:
        for path in sorted(I18N_DIR.glob('*.po')):
            lang = path.stem
            try:
                overlay = _read_po(path)
            except Exception:
                # A single malformed file must not disable the other languages.
                _logger.exception("%s: unreadable, skipped", path.name)
                continue
            touched = set()
            total = 0
            for (kind, module), translations in sorted(overlay.items()):
                key = (module, lang)
                merge = _merge_python if kind == 'python' else _merge_javascript
                merge(key, translations)
                touched.add(f"{module}[{kind}]")
                total += len(translations)
            if touched:
                _logger.info("%s: merged %s code term(s) for %s into language %s",
                             path.name, total, ', '.join(sorted(touched)), lang)
        _applied = True
    except Exception:
        _applied = False
        _logger.exception(
            f"{MODULE_NAME}: could not install the code translation overlay; "
            "data translations are unaffected")


def refresh():
    """Merge again, for a registry reloaded in the same process (an upgrade
    started from the Apps menu does that): the cache lives in the process, so
    the po files have to be read again for a changed translation to be served.
    """
    global _applied
    _applied = False
    install()
