"""SNCIC translation overlay for Odoo 20.

The translations live in ``i18n/<lang>.po`` next to this file.  Odoo imports
the data terms (records, fields, views) from that file by itself when the
module is installed or upgraded, and again when the registry is fully loaded
(see :mod:`models.ir_module_module`, needed because the entries of this module
point at records of other modules); the code terms (python ``_()``, JS ``_t()``,
QWeb) are merged into the runtime cache by :mod:`code_translation_overlay`,
because Odoo only ever looks those up in the po file of the module that
declares the string.
"""

from . import code_translation_overlay
from . import models

code_translation_overlay.install()


def post_load():
    """Called by Odoo after this module is imported (manifest ``post_load``).

    Applying the overlay here as well makes the module independent of the import
    order: the merge is idempotent, so a second run simply recomputes the same
    result with the official translations now guaranteed to be loaded.
    """
    code_translation_overlay.install()
