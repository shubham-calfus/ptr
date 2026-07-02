"""Per-control-type split of the helpers_v2 runtime. See helpers_v2.py.

Importing the facade here is LOAD-BEARING, not cosmetic. Each submodule runs
``from .. import helpers_v2 as facade`` at import time, which re-enters
helpers_v2. If something that walks the package tree (e.g. aetherion's
``discover(walk=True)``) imports a submodule such as ``helpers.core`` BEFORE
helpers_v2, that submodule pauses mid-import to load helpers_v2; helpers_v2's
body then runs ``from .helpers.core import *`` against the still-partial core
(no ``__all__``/functions defined yet -> nothing imported) and reaches its
bottom-of-file side-effects (``atexit.register(_act_write_diagnostics)`` ...)
with those names unbound -> NameError.

Forcing the facade to load first here makes import order irrelevant: importing
ANY submodule triggers this __init__, which fully loads helpers_v2 (which in
turn fully loads every submodule) before the submodule body continues. The
helpers_v2-first path (the ``from src.runtime.helpers_v2 import *`` injection in
tools.py) is unaffected -- helpers_v2 is already in sys.modules, so this is a
no-op rebind of the module object.
"""

from .. import helpers_v2 as _facade  # noqa: F401  (force the facade to load first)
