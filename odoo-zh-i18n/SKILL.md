---
name: odoo-zh-i18n
description: Find the strings an Odoo addons tree still shows untranslated in Chinese (or another language), translate them into the project's own overlay module, and report whether an instance really serves them. Use when an Odoo UI displays English text, when asked to translate Odoo modules or check translation coverage, and to keep translations across upstream synchronisations.
metadata:
  short-description: Translate missing Odoo zh_CN strings
---

# Odoo zh_CN translation

An Odoo UI shows English when a term has no translation in the module's
`i18n/zh_CN.po`. This skill turns that gap into a repeatable loop:
**scan the source -> translate the worklist -> check -> build -> load -> verify**.

## The one hard rule

**Never write into the upstream source tree.** Translations applied to
`odoo/addons/<module>/i18n/<lang>.po` are lost the next time the tree is
synchronised (`git reset --hard`, `git pull` with conflicts, a fresh clone).
Everything this skill produces lives in the project's own addons repository:

| what | where |
| --- | --- |
| the translations, installable | `<project>/addons/<overlay module>/i18n/<lang>.po` |
| the editable archive (source of truth) | `<project>/addons/translations/<lang>/<module>.po` |

For `odoo20tbb` the overlay module is `sn_odoo20_translations`; for another
project pick the same shape (`sn_odoo<version>_translations`) and keep the
manifest convention of that project (`version <version>.x.y.z`, `author`,
`license`). Only the overlay module's po file is ever written by this skill;
`i18n_apply.py build --module-dir` refuses to run without `__manifest__.py`, and
no subcommand of the scripts takes an upstream path.

## The two channels (read this first)

Odoo stores the two kinds of terms in completely different places. Getting this
wrong is the usual reason a translation "does not appear".

- **Code terms** -- python `_()` / `_lt()`, JS/QWeb `_t()`, spreadsheet
  dashboards. Never stored in the database: `odoo.tools.translate` reads them
  from the po file of the module that *declares* the string, at runtime, and
  only from entries carrying the `#. odoo-python` / `#. odoo-javascript`
  comment. There is no cross-module fallback, so a po entry in the overlay
  cannot translate another module's code term. The overlay therefore merges its
  own entries into `odoo.tools.translate.code_translations`
  (`code_translation_overlay.py`) when it is loaded.
- **Data terms** -- records, field labels, views, mail templates. Stored in the
  database (`jsonb` columns), imported from po files by
  `ir.module.module._load_module_terms`. The overlay imports its own file again
  once the registry is complete (`models/ir_module_module.py`), because Odoo's
  own import runs while the module graph is still loading and skips the entries
  whose model is not registered yet -- which is every module loaded after the
  overlay.

Both are loaded from the overlay's `i18n/<lang>.po`: code terms when the module
is imported, data terms when the registry finishes loading. Nothing else has to
be called, and no upstream file is touched. `references/odoo-i18n-mechanics.md`
has the details (file precedence, fuzzy entries, node-wise merging of HTML,
what makes an entry silently useless) worth reading before debugging anything.

## Setting the overlay up for a new project

`assets/overlay-module/` is the whole module, ready to copy; the only file to
edit is its manifest:

```bash
cp -r "$SCRIPTS/../assets/overlay-module" "$PROJECT/addons/sn_odoo19_translations"
# then set `name`, `version` (<version>.x.y.z) and keep `post_load`
```

Everything else derives the technical module name from its own directory name,
so renaming the directory is enough. Install it once with the service stopped
(`-i sn_odoo<version>_translations --stop-after-init`), then use the workflow
below. The two files worth reading before trusting it on another Odoo version
are `code_translation_overlay.py` (runtime cache shape) and
`models/ir_module_module.py` (the late data import) -- both fail soft: they log
a warning and leave the rest of the translations working.

## Workflow

Every project on this machine is a self-contained stack; read the project's
`AGENTS.md` / `README.md` for its addons path, database, venv, config file and
systemd unit. Run the scripts with the **Odoo venv python** (it has `polib`):

```bash
VENV=/home/xfusion/venvs/odoo20
ROOT=/home/xfusion/projects/odoo/odoo20tbb          # the project directory
OVERLAY="$ROOT/addons/sn_odoo20_translations"       # the overlay module
WORKLISTS="$ROOT/addons/translations"               # the archive, one dir per language
UNIT=odoo@odoo20tbb
DB=odoo20
CONF=/home/xfusion/etc/odoo/odoo20tbb.conf
SCRIPTS="${CODEX_HOME:-$HOME/.codex}/skills/odoo-zh-i18n/scripts"
```

1. **Scan.** Terms the shipped `.pot` lists but no po translates are the ones
   missing from the UI. Restrict to what the database actually runs, and keep
   the noise out:

   ```bash
   $VENV/bin/python "$SCRIPTS/i18n_scan.py" \
       --addons "$ROOT/odoo/addons" --addons "$ROOT/addons" \
       --overlay-module "$OVERLAY" \
       --installed-db "$DB" --summary \
       --out-dir "$WORKLISTS/zh_CN"
   ```

   One worklist per module is written to `--out-dir`; re-scanning preserves what
   a worklist already contains, and `--overlay-module` counts the overlay's
   entries as translated, so the lists only ever hold what is genuinely missing.
   `--installed-db` needs `psql`; drop it to scan the whole tree. Default
   `--categories` is `code,field,terms`; `terms_html` (views with markup),
   `description` (module store copy) and `other` are opt-in, see `--categories`.

   Keep the list of decisions already taken in
   `$WORKLISTS/zh_CN/_intentional.txt` (one msgid per line, optional `# reason`;
   read automatically from `--out-dir`): brand names, paper sizes, single-letter
   keyboard shortcuts, JS placeholders. Those terms are skipped by the scan, so
   the coverage number stays "work left" instead of "decisions taken" -- and the
   scan re-checks the file on every run, reporting terms that are translated now
   or absent from the source so they can be dropped. Add a term there only when
   a Chinese translation would be wrong, never to make a number look better.

2. **Reuse what the tree already translated** (`fill` only touches entries the
   whole tree translates in exactly one way, so it never has to guess):

   ```bash
   $VENV/bin/python "$SCRIPTS/i18n_tm.py" fill "$WORKLISTS/zh_CN" \
       --addons "$ROOT/odoo/addons"
   ```

3. **Translate the rest.** Fill the `msgstr` fields in the worklist files.
   Follow `references/zh_CN-style.md`: keep placeholders, markup, quote style and
   leading whitespace byte identical, use the terminology Odoo already uses, and
   judge each term from its `#:` occurrence. Never translate module descriptions,
   brand names, or strings whose context is unclear without looking up the
   source file. Prefer editing many entries at a time with a script over
   hand-editing large .po files, and let step 4 prove the structure survived.

4. **Check.** `i18n_apply.py check` validates that every translation kept its
   placeholders and markup tags, is not left in English, and did not change line
   breaks, and that `_intentional.txt` does not contradict a worklist. Fix what
   it reports instead of lowering the bar; whether a declaration is still needed
   at all is the scan's answer, since only it reads the source.

   ```bash
   $VENV/bin/python "$SCRIPTS/i18n_apply.py" check "$WORKLISTS/zh_CN"
   ```

5. **Build** the overlay module's po file from the worklists (`--write`; without
   it, a dry run). Worklists are cumulative and the file is regenerated in full,
   so this is also how an upstream pull is absorbed:

   ```bash
   $VENV/bin/python "$SCRIPTS/i18n_apply.py" build "$WORKLISTS/zh_CN" \
       --module-dir "$OVERLAY" --write
   ```

   The build fails instead of writing when one msgid is translated two
   different ways, because a po file can only hold one; unify the worklists.

6. **Load.** Stop the unit, upgrade, start it again (the project AGENTS.md rule:
   never run `-i` / `-u` while the service is up), then check the service and the
   login page:

   ```bash
   sudo systemctl stop "$UNIT"
   (cd "$ROOT/odoo" && $VENV/bin/python odoo-bin -c "$CONF" -d "$DB" \
        -u sn_odoo20_translations --stop-after-init)   # exit code must be 0
   sudo systemctl start "$UNIT"
   systemctl is-active "$UNIT"                        # active
   curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8070/web/login   # 200
   ```

   A plain `systemctl restart "$UNIT"` is enough when only the po file changed:
   the overlay re-imports its data terms during the registry load when the po
   files are not the ones it already imported. Use `-u` whenever the module's
   code or manifest changed (a new language file, the `models/` directory, the
   manifest itself).

7. **Verify** that the terms are really gone from the untranslated list, that the
   database holds them, and that the runtime serves them -- the po file can be
   correct while the `#. odoo-python` comment is missing and the UI stays
   English. Three checks, all three cheap:

   ```bash
   # a) the overlay is loaded and merged
   grep "sn_odoo20_translations" /home/xfusion/logs/odoo20tbb.log | tail -3
   # b) a data term is stored                                    (psql -d odoo20)
   #    SELECT name->>'zh_CN' FROM ir_model_fields WHERE name = '<field>';
   # c) a code term reaches the runtime
   cd "$ROOT/odoo"
   $VENV/bin/python odoo-bin shell -c "$CONF" -d "$DB" --no-http <<'EOF'
   from odoo.tools.translate import code_translations
   print(code_translations.get_python_translations('<module>', 'zh_CN'))
   EOF
   ```

   Then re-scan (step 1): the terms you filled must be gone.

## After an upstream pull

Nothing to repair: the overlay is a module of the project's own addons, and
`git reset --hard` in `odoo/` cannot reach it. Only new terms need work:

```bash
$VENV/bin/python "$SCRIPTS/i18n_scan.py" ... --overlay-module "$OVERLAY" --summary \
    --out-dir "$WORKLISTS/zh_CN"                 # lists what upstream added since
$VENV/bin/python "$SCRIPTS/i18n_apply.py" build "$WORKLISTS/zh_CN" \
    --module-dir "$OVERLAY" --write
sudo systemctl restart "$UNIT"
```

If the overlay module itself was never installed in that database, install it
once with `-i sn_odoo20_translations --stop-after-init` (service stopped).

## Files

- `scripts/i18n_scan.py` -- write the worklists (also `--out` for one combined
  file, and stdout when neither is given).
- `scripts/i18n_tm.py` -- `stats` / `fill` / `lookup` against the translations
  already present elsewhere in the tree.
- `scripts/i18n_apply.py` -- `check` and `build` (never touches upstream).
- `scripts/i18n_common.py` -- shared helpers, imported by the three above.
- `references/odoo-i18n-mechanics.md` -- how Odoo resolves, imports and caches
  translations, and what the overlay module has to do about it; read when a
  change does not show up.
- `references/zh_CN-style.md` -- terminology, style rules, and the checks the
  scripts enforce.
- `assets/overlay-module/` -- the overlay module to copy into a project's
  `addons/` (`__manifest__.py`, `code_translation_overlay.py`,
  `models/ir_module_module.py`).

The module implemented for `odoo20tbb` is
`/home/xfusion/projects/odoo/odoo20tbb/addons/sn_odoo20_translations` (committed
to that project's addons repository, along with its worklists under
`addons/translations/`); it is the living example of this skill.
