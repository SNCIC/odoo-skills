"""Import the overlay's data translations once the registry is complete.

Odoo imports the ``model:`` / ``model_terms:`` entries of a module po file from
``ir.module.module._load_module_terms``, which runs *inside* the module loading
loop.  ``TranslationImporter`` skips every row whose model is not registered
yet (``if model_name not in self.env: continue``), and this module is loaded
early -- it only depends on ``base`` -- so its entries for records of other
modules (mail templates, payment providers, products, ...) were silently
dropped.  Only the ``base`` models could ever be translated that way.

The registry is complete when Odoo calls ``_register_hook`` on every model at
the end of the loading process (``odoo/modules/loading.py``, "STEP 9: call
_register_hook on every model"), so the import is repeated here.  It is
idempotent and cheap, and it is skipped altogether while the po files are
unchanged, so a service start costs nothing once the translations are loaded.

The import overwrites what the database already holds for the terms of this po
file: the po is the source of truth of this module, and correcting a translation
means editing it, which an ``overwrite=False`` import would silently ignore.
Only the terms listed here are affected, and only for records that are not
``noupdate``; deleting an entry hands its term back to upstream.

A module upgrade imports that module's own po file *after* these terms were
written, and with ``overwrite=False``, so it cannot undo them -- but the upgrade
itself can: re-reflecting a field (``ir.model.fields``, ``ir.model``) rewrites
its source value and the other languages go with it, leaving the record English
again while the entry is still in the po.  The worklists would then look
complete and the instance would still serve the source text, which is why the
digest does not stand alone: the import also repeats whenever a module row
changed, i.e. after any install, upgrade or uninstall.
"""

import hashlib
import logging
from pathlib import Path

from odoo import models

from .. import code_translation_overlay

_logger = logging.getLogger(__name__)

MODULE_DIR = Path(__file__).resolve().parents[1]
MODULE_NAME = MODULE_DIR.name          # the addon directory name Odoo uses
I18N_DIR = MODULE_DIR / 'i18n'
SIGNATURE_PARAM = f'{MODULE_NAME}.po_signature'


def po_signature():
    """A digest of every po file of this module, changed when they change."""
    digest = hashlib.sha256()
    for path in sorted(I18N_DIR.glob('*.po')):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def module_marker(env):
    """A marker of the module table, changed by any install/upgrade/removal.

    An upgrade can leave a term English again without touching the po file (see
    the module docstring), so the marker travels with the digest.
    """
    env.cr.execute("SELECT count(*), max(write_date) FROM ir_module_module")
    count, write_date = env.cr.fetchone()
    return f'{count}:{write_date}'


class IrModuleModule(models.Model):
    _inherit = 'ir.module.module'

    def _register_hook(self):
        result = super()._register_hook()
        try:
            params = self.env['ir.config_parameter'].sudo()
            signature = f'{po_signature()}|{module_marker(self.env)}'
            if params.get_str(SIGNATURE_PARAM) != signature:
                langs = [code for code, _name in self.env['res.lang'].get_installed()]
                if langs:
                    self.env['ir.module.module']._load_module_terms(
                        [MODULE_NAME], langs, overwrite=True)
                code_translation_overlay.refresh()
                params.set_str(SIGNATURE_PARAM, signature)
        except Exception:
            # A broken overlay must not keep the registry from loading: the
            # translations that did make it are still in the database.
            _logger.exception(
                "%s: could not import the data translations of %s",
                MODULE_NAME, MODULE_NAME)
        return result
