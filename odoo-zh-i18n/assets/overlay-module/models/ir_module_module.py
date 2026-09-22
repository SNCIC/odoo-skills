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


class IrModuleModule(models.Model):
    _inherit = 'ir.module.module'

    def _register_hook(self):
        result = super()._register_hook()
        try:
            params = self.env['ir.config_parameter'].sudo()
            signature = po_signature()
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
