"""Architecture guards for the facade-routed split of helpers_v2.

helpers_v2.py was split into a `src/runtime/helpers/` package (one module per
Oracle control type + core/recovery/oracle_nav/dispatch). To keep behaviour
identical to the original single module, every submodule references shared
helpers and `_ACT_*` state through the facade object `facade` (== src.runtime.helpers_v2)
at call time. These tests freeze that contract so a future refactor cannot
silently:
  * change the `import *` ABI the generated recordings depend on,
  * break monkeypatch propagation (the 783 `monkeypatch.setattr(helpers_v2, ...)`
    in test_runtime_helpers_v2.py rely on submodule calls resolving via the facade),
  * reintroduce inter-submodule imports (which would create import cycles), or
  * duplicate the shared `_ACT_*` global state into a submodule namespace.
"""

import ast
import importlib
import pkgutil
import subprocess
import sys
from pathlib import Path

import src.runtime.helpers as helpers_pkg
import src.runtime.helpers_v2 as helpers_v2

_PROJECT_ROOT = Path(__file__).resolve().parents[1]

# The exact public surface `from src.runtime.helpers_v2 import *` must expose.
# This is the ABI the AST-prepared recordings call into — changing it is a
# breaking change and must be deliberate.
PUBLIC_STAR_NAMES = {
    "_act_ai_extract",
    "_act_capture_failure",
    "_act_check_target",
    "_act_click_button_target",
    "_act_click_combobox",
    "_act_click_listbox_option",
    "_act_click_navigation_button",
    "_act_click_numeric_button_target",
    "_act_click_table_field",
    "_act_click_table_row",
    "_act_click_text_target",
    "_act_click_textbox",
    "_act_dblclick_text_target",
    "_act_fill_textbox",
    "_act_goto_page",
    "_act_launch_chromium",
    "_act_login_submit_and_redirect",
    "_act_pick_date_via_icon",
    "_act_raw_click",
    "_act_raw_fill",
    "_act_raw_press",
    "_act_register_page",
    "_act_resolve",
    "_act_select_adf_menu_panel_option",
    "_act_select_combobox_option",
    "_act_select_option_target",
    "_act_select_search_trigger_option",
    "_act_set_script_data",
    "_act_submit_textbox_enter",
    "_act_tracked_action",
    "_act_tracked_raw_action",
    "_act_uncheck_target",
    "_act_wait_after_interaction",
    "_act_wait_for_post_login_redirect",
    "_act_wait_ms",
    "_act_write_diagnostics",
}


def _submodule_names():
    return sorted(
        m.name for m in pkgutil.iter_modules(helpers_pkg.__path__) if not m.name.startswith("_")
    )


def _submodule_paths():
    pkg_dir = Path(helpers_pkg.__file__).parent
    return sorted(p for p in pkg_dir.glob("*.py") if p.name != "__init__.py")


def test_star_import_surface_is_frozen():
    """`from helpers_v2 import *` must expose exactly the recorded-script ABI."""
    assert set(helpers_v2.__all__) == PUBLIC_STAR_NAMES


def test_all_submodule_functions_are_reexported_on_facade():
    """Every helper in the package is the same object as the facade attribute,
    so explicit `from helpers_v2 import _act_x` and `helpers_v2._act_x` both work."""
    for name in _submodule_names():
        mod = importlib.import_module(f"src.runtime.helpers.{name}")
        for fn_name in getattr(mod, "__all__", []):
            assert hasattr(helpers_v2, fn_name), f"{fn_name} not re-exported on facade"
            assert getattr(helpers_v2, fn_name) is getattr(mod, fn_name)


def test_submodule_functions_route_through_the_facade():
    """Each submodule function's module globals bind `facade` to the facade module,
    so a `facade.X` lookup hits the facade dict — this is what makes monkeypatching
    helpers_v2.X and shared _ACT_* state behave like the original single module.
    `facade` is used (not `helper`) because `helper` is a common parameter name."""
    for name in _submodule_names():
        mod = importlib.import_module(f"src.runtime.helpers.{name}")
        assert getattr(mod, "facade", None) is helpers_v2, f"{name}.facade is not the facade"
        for fn_name in getattr(mod, "__all__", []):
            fn = getattr(mod, fn_name)
            assert fn.__globals__.get("facade") is helpers_v2


def test_submodules_do_not_import_each_other():
    """Submodules may import only stdlib/playwright and the facade. Any
    inter-submodule import would create an import cycle (the families are
    mutually recursive) and defeat the facade-routing invariant."""
    for path in _submodule_paths():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                # allowed: `from .. import helpers_v2 as facade` /
                # `from src.runtime import helpers_v2`
                if node.module == "helpers_v2" or (node.module or "").endswith(".helpers_v2"):
                    continue
                imported = {a.name for a in node.names}
                if node.level >= 1 and "helpers_v2" in imported:
                    continue
                # stdlib / third-party absolute imports are fine; reject sibling helpers.
                mod = node.module or ""
                assert "helpers." not in mod and not (node.level and mod and mod != ""), (
                    f"{path.name} imports from a sibling helpers submodule: "
                    f"level={node.level} module={mod!r} names={imported}"
                )


def test_shared_mutable_state_lives_only_on_the_facade():
    """The _ACT_* module globals must be defined on the facade only; submodules
    must not redefine (shadow) them, or shared state would fork per-module."""
    facade_globals = {
        n
        for n in vars(helpers_v2)
        if n.startswith("_ACT_") and not callable(getattr(helpers_v2, n))
    }
    assert "_ACT_CURRENT_STRATEGY" in facade_globals  # sanity: state is on the facade
    for path in _submodule_paths():
        tree = ast.parse(path.read_text())
        assigned = set()
        for node in tree.body:  # module-level assignments only
            if isinstance(node, ast.Assign):
                assigned.update(t.id for t in node.targets if isinstance(t, ast.Name))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                assigned.add(node.target.id)
        leaked = {a for a in assigned if a.startswith("_ACT_")}
        assert not leaked, f"{path.name} redefines shared state: {leaked}"


def test_any_submodule_can_be_imported_first():
    """Importing a submodule BEFORE helpers_v2 must fully load the facade.

    aetherion's ``discover(walk=True)`` imports package modules in tree order,
    so it can import e.g. ``src.runtime.helpers.core`` before helpers_v2. Each
    submodule re-enters helpers_v2 via ``from .. import helpers_v2 as facade``;
    if the facade body runs while the submodule is still partially imported, its
    ``from .helpers.<mod> import *`` pulls nothing and its bottom-of-file
    side-effects raise ``NameError: _act_write_diagnostics``. The package
    __init__ forces the facade to load first to make import order irrelevant.

    Must run in a fresh interpreter: helpers_v2 is already in this process's
    sys.modules, so an in-process import would not reproduce the cold start.
    """
    for name in _submodule_names():
        code = (
            f"import src.runtime.helpers.{name}; "
            "import src.runtime.helpers_v2 as f; "
            "assert hasattr(f, '_act_write_diagnostics'); "
            "assert len(f.__all__) == 36"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=_PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, (
            f"importing helpers.{name} first failed:\n{proc.stderr}"
        )
