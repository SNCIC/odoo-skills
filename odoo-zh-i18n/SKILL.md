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

### Windows dev machine (this project's second host)

The scripts call `psql` **without `-h`/`-U`**, so on Windows the connection
falls back to the OS account, fails, and the failure path breaks twice over:
`psql` prints its error in the console code page (GBK on a Chinese locale),
`subprocess.run(text=True)` decodes it as UTF-8 and dies inside the reader
thread, and the script then reports
`AttributeError: 'NoneType' object has no attribute 'strip'` -- an error that
names neither the encoding nor the connection. Export the `PG*` variables
first; `PGCLIENTENCODING=UTF8` keeps the *output* decodable once connected:

```bash
export PATH="/d/Program Files/PostgreSQL/17/bin:$PATH"
export PGHOST=127.0.0.1 PGPORT=5432 PGUSER=odoo PGDATABASE=odoo20tbb
export PGCLIENTENCODING=UTF8
export PGPASSWORD=$(python -c "import configparser;c=configparser.ConfigParser();\
c.read(r'D:/odoo/odoo20tbb/odoo20.conf');print(c['options']['db_password'])")
```

Other differences from the Linux host: the venv is
`$ROOT/.venv/Scripts/python.exe` (not `bin/`), the overlay module lives in the
project's second addons root `$ROOT/myaddons/` (worklists in
`$ROOT/myaddons/translations/`), the config file is `$ROOT/odoo20.conf`, and
the service is stopped with `bash "$ROOT/tmp/stop_odoo20.sh"` rather than
systemd. Use the installed skill copy under `~/.workbuddy/skills/odoo-zh-i18n`
when running from a WorkBuddy session.

1. **Scan the source.** Terms the shipped `.pot` lists but no po translates are
   the ones missing from the UI. Restrict to what the database actually runs,
   and keep the noise out:

   ```bash
   $VENV/bin/python "$SCRIPTS/i18n_scan.py" \
       --config "$CONF" --root "$ROOT" \
       --overlay-module "$OVERLAY" \
       --installed-db "$DB" --summary \
       --out-dir "$WORKLISTS/zh_CN"
   ```

   `--config` + `--root` are the pair that matters. The script asks Odoo itself
   for the addons path (it runs `initialize_sys_path()` and imports
   `odoo.addons`) and therefore scans every directory the instance really
   loads: `odoo/odoo/addons` -- where **`base` lives** -- the project's
   `addons/`, and the data_dir `addons/<version>` directory. Without the config
   that same call resolves Odoo's *default* data_dir (`~/.local/share/Odoo`,
   not the project's), so pass it even when `--root` is obvious. Hand-written
   `--addons` flags are the fallback: they probe neighbouring `odoo/addons`
   directories, which usually recovers `base`, but a directory outside that
   shape leaves a whole module invisible -- exactly the kind of gap that looks
   like "nothing left to translate". The summary therefore names the installed
   modules that are *not* below the scanned dirs, and the audit in step 2
   answers for them independently.

   One worklist per module is written to `--out-dir`; re-scanning preserves what
   a worklist already contains, and `--overlay-module` counts the overlay's
   entries as translated, so the lists only ever hold what is genuinely missing.
   The fragment header keeps the previous scan's dates, so a re-scan that
   changes nothing leaves `git diff` empty. `--installed-db` needs `psql`; drop
   it to scan the whole tree -- it is combined with `--modules` as a *union*,
   so passing both widens the run back to every installed module; narrow with
   one of them. Default `--categories` is
   `code,field,terms,terms_html`; `terms_html` (markup inside view archs, where
   hundreds of strings live) used to be opt-in and hid those terms when it was
   forgotten. `description` (the module store copy) and `other` stay opt-in.

   Keep the list of decisions already taken in
   `$WORKLISTS/zh_CN/_intentional.txt` (one msgid per line, optional
   `# reason`, and `\n` / `\t` / `\\` escapes for a msgid that spans lines; read
   automatically from `--out-dir`): brand names, paper sizes, single-letter
   keyboard shortcuts, JS placeholders. Those terms are skipped by the scan, so
   the coverage number stays "work left" instead of "decisions taken" -- and the
   scan re-checks the file on every run, reporting terms that are translated now
   or absent from the source so they can be dropped. Add a term there only when
   a Chinese translation would be wrong, never to make a number look better.

   **Declaring a term does not remove it from a worklist that already has it.**
   The scan skips what the file declares, but nothing deletes the empty entry an
   earlier scan wrote into `<module>.po` -- it stays behind as a zombie that
   `check` / `build` keep reporting. After adding terms to `_intentional.txt`,
   drop the matching empty entries from the worklists once:

   ```python
   intentional = read_intentional([worklists_dir])
   for po in worklists_dir.glob('*.po'):
       doc = polib.pofile(str(po))
       for e in [e for e in doc if e.msgid and not e.msgstr.strip()
                 and e.msgid in intentional]:
           doc.remove(e)
       doc.save(str(po))
   ```

   A project that has accumulated worklists over several rounds is the case
   where this matters: `odoo20tbb` had 88 such zombies (50 `base`, 25
   `html_builder`, 7 `website`, 6 `website_sale`).

2. **Scan the instance.** The source scan only knows what a `.pot` lists, so
   it is blind to three things: a module that ships no pot at all, a pot older
   than the source, and a record whose po entry is missing or empty *for that
   record* while the same English text is translated elsewhere. Ask the
   database (needs `psql`, and the venv python from inside the source tree so
   Odoo's own HTML-node helpers can be imported):

   ```bash
   (cd "$ROOT/odoo" && $VENV/bin/python "$SCRIPTS/i18n_db_audit.py" \
       --db "$DB" --config "$CONF" --root "$ROOT" \
       --overlay-module "$OVERLAY" --summary)
   ```

   It walks every translatable field of the instance, compares the `en_US` and
   the `zh_CN` value record by record (node by node for markup, exactly like
   the importer), and splits what it finds by what a po can do about it:

   - *translated nowhere yet* -- nobody translated the text; this is work, and
     the audit finds it even when no pot mentions the term;
   - *translated somewhere, but no po entry points at the record* (printed with
     a `+`) -- the po has the wording for some other record, this one has no
     entry naming its xmlid, so it stays English; the overlay adds the
     occurrence, and the audit's worklist reproduces the existing wording
     instead of asking for a new translation;
   - *already in a po that names the record* -- the po is fine, the instance
     never imported it: upgrade that module (the po file is reported), this is
     not translation work. For a core module nobody upgrades (`base`), the
     overlay entry is the alternative: its import runs with `overwrite=True`
     and writes the value the module's own po never delivered.

     Prefer the upgrade when the module is one the project upgrades: an overlay
     entry that duplicates an upstream one is noise from then on, and where the
     two wordings differ it silently overrides upstream's;
   - *the po names the record with a source text older than the record's value*
     (printed with a `~`) -- the entry carries the record's xmlid but an older
     `msgid`, so the pair never matches and the term looks untranslated. On a
     whole-value field (`char`, `text`, `name`, `help`) it is imported anyway,
     because such a row is keyed on its xmlid alone, and the upgrade then writes
     the wording of a text the record no longer has: read it, and correct it
     through the overlay when it does not fit any more. On markup the entry is
     merged node by node, so a stale one reaches nothing and the term stays
     ordinary work. Reported in the same list as the group above;
   - *the po ships the English text as its translation* (`msgstr` equals
     `msgid`, printed with a `=`) -- the entry names the record and a `-u`
     imports it, but what it imports is the same English. Odoo's own po files
     are full of these (brands, symbols, demo values, `PDF`, `X`), and they are
     *decisions*, not gaps: either the term deserves translating after all
     (then the overlay is the way to say so) or it belongs in
     `_intentional.txt`. They are never worklists. Two thirds of a raw audit of
     `odoo20tbb` was this group, which is why the audit reports it separately:
     counted as "translated" it hides the record, counted as "work" it buries
     the real gaps under symbols.

   A fifth line covers the terms whose po entry *cannot* arrive: the record is
   `noupdate` in `ir_model_data`, and `TranslationImporter.save()` only
   overwrites such a record with `force_overwrite`, which no module upgrade
   passes. Upgrading the module changes nothing there -- the value has to be set
   in the UI (or with a script), so the audit marks those rows `%` and reports
   them apart from "upgrade the module".

   Only the first two groups are worklists. `--out-dir "$WORKLISTS/zh_CN"`
   writes them in the usual shape for the check/build flow, `--module a,b`
   narrows the audit, `--list-unserved N` shows which po file holds the third
   group (`-`), the fourth (`=`) and the noupdate rows (`%`). `--with-user-data` adds records a user created (no xmlid, so no po
   can reach them) and `--with-module-description` the long Apps-store blurb,
   which no language's po translates. A module's `shortdesc` and `summary` are
   reported like any other term -- the instance stores and renders them -- so a
   module whose po entry for `base.module_<name>` is empty is a real gap in the
   Apps list.

   The `%` rows are the one group no upgrade can reach: their record is
   `noupdate` in `ir_model_data`, and `TranslationImporter.save()` overwrites
   such a record only with `force_overwrite`, which no module upgrade passes.
   The upstream po usually has the wording -- the audit prints the file that
   holds it.  Put those entries alone in a small po (keep their occurrences,
   drop the ones naming other records) and import it with `force_overwrite`
   from a shell:

   ```bash
   (cd "$ROOT/odoo" && $VENV/bin/python odoo-bin shell -c "$CONF" -d "$DB" --no-http <<'EOF'
   from odoo.tools.translate import TranslationImporter
   importer = TranslationImporter(env.cr, verbose=False)
   with open('/tmp/frozen.po', 'rb') as fh:
       importer.load(fh, 'po', 'zh_CN')   # not load_file(): it opens through
   importer.save(force_overwrite=True)    # file_open, which refuses a path
   env.cr.commit()                        # outside the addons roots, silently
   EOF
   )
   ```

   Keep that po small: `force_overwrite` writes every term it holds, including
   the ones a po already delivered.

2b. **Scan the source for code terms no pot lists.** A pot is a snapshot, and a
   string added to the source after it was generated is in no pot -- invisible
   to step 1, and to step 2 as well, since a code term is never stored in the
   database.  ``Shortcuts`` in the web user menu was exactly that: the entry is
   next to ``Help`` and ``My Preferences``, which the pot lists and zh_CN
   translates.  This reads the source with Odoo's own extractors and keeps what
   neither the declaring module's po nor the overlay delivers:

   ```bash
   $VENV/bin/python "$SCRIPTS/i18n_code_audit.py" \
       --config "$CONF" --root "$ROOT" --overlay-module "$OVERLAY" \
       --installed-db "$DB" --lang zh_CN --summary
   ```

   It needs no database of its own (`--installed-db` only asks `psql` which
   modules to report; drop it to scan every module of the addons directories).
   Findings are not written to a worklist: a code entry needs the
   `#. odoo-python` / `#. odoo-javascript` comment, which the runtime -- not the
   occurrence -- reads, and an entry with the wrong one translates nothing while
   looking perfect.  Add the entry by hand with the file and line the report
   gives, and let `check` prove the archive survived.

3. **Reuse what the tree already translated** (`fill` only touches entries the
   whole tree translates in exactly one way, so it never has to guess):

   ```bash
   $VENV/bin/python "$SCRIPTS/i18n_tm.py" fill "$WORKLISTS/zh_CN" \
       --addons "$ROOT/odoo/addons"
   ```

   **Read what `fill` wrote before trusting it.** "The only way the tree
   translates this term" is sometimes *not a translation at all*: when the sole
   occurrence elsewhere is itself left in English, fill copies the source into
   `msgstr` and the entry reads as translated while shipping the English text.
   `odoo20tbb` picked up `Paypal` and `%(property_string)s (%(parent_name)s)`
   that way. Blank any `msgstr` that equals its `msgid` **and** is declared in
   `_intentional.txt` -- those are decisions, and the build skips an empty
   `msgstr` anyway.

4. **Translate the rest.** Fill the `msgstr` fields in the worklist files.
   Follow `references/zh_CN-style.md`: keep placeholders, markup, quote style and
   leading whitespace byte identical, use the terminology Odoo already uses, and
   judge each term from its `#:` occurrence. Never translate module descriptions,
   brand names, or strings whose context is unclear without looking up the
   source file. Prefer editing many entries at a time with a script over
   hand-editing large .po files, and let step 5 prove the structure survived.

   Filling a worklist does not change the scan's count: the archive is not a
   po file the instance reads. The number drops after step 6, when the filled
   archive is built into the overlay's `i18n/<lang>.po`.

5. **Check.** `i18n_apply.py check` validates that every translation kept its
   placeholders and markup tags, is not left in English, and did not change line
   breaks, and that `_intentional.txt` does not contradict a worklist. Fix what
   it reports instead of lowering the bar; whether a declaration is still needed
   at all is the scan's answer, since only it reads the source.

   ```bash
   $VENV/bin/python "$SCRIPTS/i18n_apply.py" check "$WORKLISTS/zh_CN"
   ```

   Worklists are cumulative, so a bare `check` reprints every warning ever
   accepted (labels that stay English on purpose, `"%s:"` -> `"%s："`, a CSS
   blob) and hides the ones this round introduced. Pass the state that was
   already reviewed to see only the new messages -- the overlay's own po file,
   or a ref from before the work:

   ```bash
   $VENV/bin/python "$SCRIPTS/i18n_apply.py" check "$WORKLISTS/zh_CN" \
       --baseline "$OVERLAY/i18n/zh_CN.po"          # or --baseline HEAD:i18n/zh_CN.po
   ```

6. **Build** the overlay module's po file from the worklists (`--write`; without
   it, a dry run). Worklists are cumulative and the file is regenerated in full,
   so this is also how an upstream pull is absorbed:

   ```bash
   $VENV/bin/python "$SCRIPTS/i18n_apply.py" build "$WORKLISTS/zh_CN" \
       --module-dir "$OVERLAY" --write
   ```

   The build fails instead of writing when one msgid is translated two
   different ways, because a po file can only hold one; unify the worklists.

   A fresh app chain makes this common: its first scan translates shared
   vocabulary -- control-panel summaries, `Formatted Number`, `Res Access
   Read` -- that the established modules have been wording differently for
   rounds, and the build stops on every one of them. Resolve by **keeping the
   wording of the module that has been in the worklists longest and making the
   new module yield** (the established wording is what the rest of the UI
   already shows; the new module has not shipped yet). Read the authoritative
   `msgstr` out of that module's worklist by msgid and write it over every
   other occurrence -- never retype it, the two often differ by one
   look-alike character (`-` vs `‑`, `&` vs `&amp;`). `odoo20tbb` unified 5
   conflicts this way.

7. **Load.** Stop the unit, upgrade, start it again (the project AGENTS.md rule:
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
   files are not the ones it already imported, and also when a module was
   installed, upgraded or removed since: the upgrade re-reflects its records and
   the terms the overlay had written go English again, which the po files alone
   cannot show. A term the overlay owns that stays English after a restart means
   the recorded state in `ir.config_parameter` still matches -- delete
   `sn_odoo<version>_translations.po_signature` and restart to force the import.
   Use `-u` whenever the module's code or manifest changed (a new language file,
   the `models/` directory, the manifest itself).

8. **Verify** that the terms are really gone from the untranslated list, that the
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
   # web/JS terms live under their own keys and have a different shape:
   print(code_translations.get_web_translations('<module>', 'zh_CN'))
   # -> {'messages': ({'id': ..., 'string': ...}, ...)}, not a {msgid: msgstr}
   #    mapping; looking a JS msgid up with the python shape finds nothing and
   #    makes a working translation look missing.
   EOF
   ```

   Then re-scan (step 1) and re-audit (step 2): the terms you filled must be
   gone.

## Diagnosing a whole app still showing English

Before blaming a po file, check whether the module was ever *in scope*. A
worklist set is a snapshot of one scan round: modules installed later (or
skipped by an earlier `--installed-db` run) have no worklist at all, so their
upstream po gaps are never picked up and the coverage number looks fine. This
is what an entire app being English usually is -- not a broken import.

Compare the installed set against the worklist directory:

```bash
# worklists present
ls "$WORKLISTS/zh_CN"/*.po | xargs -n1 basename | sed 's/\.po$//' | sort > /tmp/have.txt
# installed modules
psql -d "$DB" -t -A -c "SELECT name FROM ir_module_module WHERE state='installed'" | sort > /tmp/installed.txt
comm -23 /tmp/installed.txt /tmp/have.txt      # installed but never scanned
```

Also confirm the overlay really carries the module -- an empty worklist and a
worklist the overlay never received both look translated from the outside:

```bash
grep -c "odoo/addons/<module>/" "$OVERLAY/i18n/zh_CN.po"   # 0 = never covered
```

For a suite, scan the neighbours in the same call: an app's modules share
vocabulary, and the per-module counts tell you whether it is one module or a
whole chain. Remember `--modules` and `--installed-db` are a **union** -- pass
one of them, never both, when you mean to narrow.

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

- `scripts/i18n_scan.py` -- the source side: write the worklists (also `--out`
  for one combined file, and stdout when neither is given). `--config` /
  `--root` come first, `--addons` is the fallback.
- `scripts/i18n_db_audit.py` -- the instance side: what the database still
  serves in English, split into "translate this", "add the occurrence",
  "upgrade that module" and "the po keeps the English text", plus `--out-dir`
  to turn the first two into worklists.
- `scripts/i18n_code_audit.py` -- the source side of the code terms: what a
  `_()` / `_t()` / QWeb string of an installed module still serves in English
  because no po translates it, including the terms no pot lists.  Reads the
  source with Odoo's own babel extractors, so it needs the source tree and the
  venv python, but no database of its own.
- `scripts/i18n_tm.py` -- `stats` / `fill` / `lookup` against the translations
  already present elsewhere in the tree.
- `scripts/i18n_set.py` -- apply a batch of translations at once
  (`apply --map map.json`), when the wording is decided and hand-editing many
  worklists would be the slow part; run `check` afterwards anyway.
- `scripts/i18n_apply.py` -- `check` (with `--baseline`) and `build` (never
  touches upstream).
- `scripts/i18n_common.py` -- shared helpers, imported by all of the above:
  module discovery, po reading, the `_intentional.txt` parser, and
  `resolve_addons_paths()`, which is what makes `--config`/`--root` see every
  directory Odoo loads.
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
