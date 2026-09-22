# How Odoo resolves translations

Everything below was read out of the Odoo 20.0 source
(`odoo/tools/translate.py`, `odoo/cli/i18n.py`); line numbers are from
`odoo20tbb` at `b0329e93`. Check them again on another version before trusting
them blindly.

## Where a translation is read from

`get_po_paths()` (translate.py:2421) yields, in this order, for language `lang`
and each base language of it:

```
i18n/<base>.po  i18n_extra/<base>.po  i18n/<lang>.po  i18n_extra/<lang>.po
```

`get_base_langs('zh_CN')` is `['zh', 'zh_CN']` (translate.py:2407), so a
`zh.po` is read first and `zh_CN.po` overrides it, and `i18n_extra/` overrides
`i18n/` inside the same language. Each file only contributes entries with a
non-empty `msgstr`, which is why an empty `zh_CN` entry does not blank out the
`zh` one. Files are resolved through the addons path, so the first addons
directory holding a module wins: a `zh_CN.po` in the project's own addons is not
merged with the one next to the core module.

## Which entries are used by which channel

`PoFileReader.__iter__` (translate.py:1406) turns each entry into a row whose
type comes from its `#:` occurrences:

| occurrence | row type | consumed by |
| --- | --- | --- |
| `code:addons/<path>:<line>` | `code` | the runtime, straight from the po file |
| `model:<model>,<field>:<module>.<xmlid>` | `model` | the module upgrade / `odoo-bin i18n import` |
| `model_terms:<model>,<field>:<module>.<xmlid>` | `model_terms` | same |

Every entry of the project's overlay po file is one of those rows: the code rows
are merged into the runtime cache by the module, the data rows are imported by
Odoo itself. See "The overlay module" below.

Two traps:

- The comment must exist and must start with `module: <name>` --
  `re.match(r"(module[s]?): (\w+)", entry.comment)` is called without a
  `None` check, so a hand-written entry without that line raises
  `AttributeError` while that module is parsed. Worklists produced by
  `i18n_scan.py` always carry it because it is copied from the pot.
- Code rows are filtered by their comment:
  `PYTHON_TRANSLATION_COMMENT = 'odoo-python'` and
  `JAVASCRIPT_TRANSLATION_COMMENT = 'odoo-javascript'` (translate.py:65, 68,
  2619, 2625). `_()`/`_lt()` terms need `#. odoo-python`, `_t()` terms, QWeb
  templates and spreadsheet dashboards need `#. odoo-javascript`. A translation
  without the right comment looks perfect in the file and never reaches the UI.
- **Only the first `#. module:` line of an entry is read.** `PoFileReader` says
  so itself (`# in case of moduleS keep only the first`, translate.py:1411) and
  uses that name as the module of every `code` row of the entry. That is fine for
  a module's own po, where the comment names one module, and wrong for the
  overlay po, which is the merged worklist of every module: one entry can carry
  the occurrences of several (`mail` and `web` both declare `Cancel`), and the
  row would be filed under whichever module happens to be listed first. The
  module of a code term therefore has to come from the occurrence path
  (`code:addons/<module>/...`, a `.py` file for python terms, anything else for
  javascript ones) -- that is what `code_translation_overlay.py` does, one merge
  per module. A term filed under one module only stays English in every other
  module's bundle.

`fuzzy` is **not** filtered: `PoFileReader` only skips `obsolete` entries, so a
fuzzy entry with a non-empty `msgstr` is used exactly like a normal one, and
`polib`'s `entry.translated()` is the wrong thing to trust when auditing
coverage. `i18n_scan.py` accordingly counts only an empty `msgstr` as
untranslated, and clears the `fuzzy` flag when it writes a real translation.

## Code terms

`CodeTranslations` (translate.py:2579) is a process-wide singleton
(translate.py:2644) that caches `{module: {src: value}}` per language, populated
lazily from the po files and never invalidated. Editing a po file therefore
changes nothing until the Odoo process is restarted. Verify what the runtime
actually has instead of assuming:

```bash
odoo-bin shell -c <conf> -d <db> --no-http <<'EOF'
from odoo.tools.translate import code_translations
print(code_translations.get_python_translations('<module>', 'zh_CN'))
print(code_translations.get_web_translations('<module>', 'zh_CN')['messages'][:5])
EOF
```

## Data terms

`TranslationImporter._load` (translate.py:2174) ignores every `code` row, skips
rows whose target field is missing or not stored, and matches records through
the xmlid in the occurrence; an xmlid no record carries (demo data, an
uninstalled localization, a renamed record) is skipped without a message.
`save(overwrite=False)` only fills values that are not stored yet --
`odoo-bin i18n import` without `-w` is safe to re-run and will not undo
translations that were edited in the UI. The overlay uses the same importer
through `ir.module.module._load_module_terms`; `odoo-bin i18n import -l zh_CN
<overlay>/i18n/zh_CN.po` remains a useful manual fallback (it runs with a
complete registry, from the CLI).

Two differences matter for the overlay, because its po file is the project's
source of truth:

- It imports with `overwrite=True`: correcting a translation means editing the
  po, and an `overwrite=False` import would silently keep the value that is
  already in the database. Only the terms the po names are affected.
- `noupdate` records are still skipped (`force_overwrite` would be needed, which
  upstream never uses outside `translate.py`), so their terms keep whatever the
  database holds -- often the English source, because Odoo stores the source
  term as the translation when it does not overwrite. A payment provider's
  message fields are such records
  (`env['ir.model.data'].search([('module', '='), ('name', '=')]).noupdate`);
  translate those in the UI if a project needs them.

A term the po names can also stay invisible for a reason that has nothing to do
with the translation: if the record's own value was edited in the database (a
customized mail template, an edited payment message), its text is not the po
`msgid` any more, and the importer has nothing to match. Comparing the worklist
entry with the record (`with_context(lang='zh_CN')`) tells the two cases apart.
The pots can also lag behind the source -- upstream regenerates them in batches
-- so a worklist entry may name a record that has not contained that term for a
while. Such an entry is harmless: it translates the module's own text and starts
working again if the term comes back, and the next scan drops it once the pot
catches up.

Check the database side directly; `translate=True` fields that are stored hold a
`jsonb` per language, and a missing language key falls back to `en_US`:

```sql
SELECT name->>'en_US', name->>'zh_CN' FROM res_country WHERE NOT (name ? 'zh_CN');
SELECT field_description->>'en_US' FROM ir_model_fields WHERE NOT (field_description ? 'zh_CN');
```

## The overlay module

Upstream po files cannot be edited (a synchronisation overwrites them), and no
single mechanism covers both channels, so the project ships its own module
(`sn_odoo20_translations` for `odoo20tbb`) holding one `i18n/<lang>.po`. What
that module has to do, and why:

- **Code terms need the runtime cache.** `CodeTranslations._get_code_translations`
  reads `get_po_paths(<the module that declares the string>, lang)`; there is no
  fallback to another module and the whole per-module dict is replaced, so a po
  entry in the overlay can never translate another module's `_()` / `_t()`
  string through files. What it can do is contribute to the shared
  `code_translations.python_translations` / `.web_translations` mappings, which
  is what `code_translation_overlay.py` does while the module is imported
  (and again from the manifest's `post_load`). The shape has to be exact:
  `frozendict` of `{src: value}` for python, and
  `frozendict({'messages': tuple(frozendict({'id', 'string'}))})` for the web.
  This is version sensitive -- the module logs a warning and leaves the cache
  alone if those attributes ever disappear.
- **Data terms need a complete registry.** `_load_module_terms` is called per
  module, inside the loading loop (`odoo/modules/loading.py:231`,
  `module._update_translations()`), and `TranslationImporter._load` drops every
  row whose model is not registered yet (`if model_name not in self.env:
  continue`). A module that only depends on `base` is loaded 4th of 73 in this
  instance, so its data entries for `mail.template`, `payment.provider`,
  `product.*`, ... were silently ignored: only `base` models (field labels,
  views, menus, module descriptions) could ever be translated that way. The
  overlay therefore imports its own po file again from
  `models/ir_module_module.py::_register_hook`, which Odoo calls once with the
  complete registry (`odoo/modules/loading.py`, "STEP 9: call _register_hook on
  every model"). A `sha256` of the po files is kept in
  `ir.config_parameter` (`sn_odoo20_translations.po_signature`) so the import
  runs only when the files actually changed; a plain service restart is enough
  after a rebuild, no `-u` needed.
- **`overwrite=False` in both paths.** The importer only fills translations that
  are not stored yet, so re-running is always safe and never undoes an edit made
  in the UI. Consequences worth knowing:
  - `model_terms` on an HTML field (`arch_db`, `body_html`, `tip_description`)
    is merged node by node against the *source* text of the record: an entry
    whose text node the record no longer has is ignored, and a node the po file
    does not translate stays in the source language. That is why a partially
    translated view can show mixed languages after a fresh import.
  - a `model:` entry only ever replaces a missing language key, never an
    existing one.

## The .pot files

`i18n/<module>.pot` is the term list upstream's bot regenerates from the source
on every commit (`git log -1 -- addons/<module>/i18n/<module>.pot`), so it is a
faithful snapshot of what the source can translate. `i18n_scan.py` diffs it
against the po, which is why the scan needs no Odoo environment and no database.

If a string is missing from both the pot and the po, the source is the problem,
not the translation: either it is not wrapped in `_()` / `_t()`, or the pot is
older than the code. Regenerate just for a look (this needs the module installed
and a database, and writes a po on stdout):

```bash
odoo-bin i18n export -c <conf> -d <db> -o - <module> > /tmp/<module>.pot
```

## What the scan deliberately skips

The raw pot-vs-po difference is dominated by noise. `i18n_scan.py` leaves out:

- msgids that already contain CJK (localisation modules such as `l10n_cn`
  legitimately use Chinese as their source language),
- msgids without a single ASCII letter (numbers, symbols, `'%s'`),
- `model:ir.module.module,description:*` -- the long store description of a
  module (`--categories description` to include it),
- markup-bearing `model_terms` entries, which need per-string care
  (`--categories terms_html`),
- everything already translated in any file of the language's search path.

A single module is a good unit of work: the worklist keeps the pot's ordering by
category (`code`, then `field`, then `terms`), which is close to the order of the
source files and keeps related terms together.
