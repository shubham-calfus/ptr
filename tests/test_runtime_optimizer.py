from src.runtime.optimizer import optimize
from src.runtime.parser import MultiLineLoop, parse_script


def test_optimize_drops_non_login_textbox_click_before_navigation_button() -> None:
    script = """
def run(playwright):
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.get_by_role("textbox", name="Notes").click()
    page.get_by_role("button", name="Continue").click()
    browser.close()
"""

    optimized = optimize(parse_script(script))

    assert not any(
        action.type == "click" and action.role == "textbox" and action.name == "Notes"
        for action in optimized
    )
    assert any(
        action.type == "navigation_button" and action.name == "Continue" for action in optimized
    )


def test_optimize_drops_terminal_non_login_textbox_click_before_close() -> None:
    script = """
def run(playwright):
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.get_by_role("textbox", name="Notes").click()
    browser.close()
"""

    optimized = optimize(parse_script(script))

    assert not any(
        action.type == "click" and action.role == "textbox" and action.name == "Notes"
        for action in optimized
    )
    assert any(action.type == "close_browser" for action in optimized)


def test_optimize_preserves_spinbutton_click_before_fill() -> None:
    script = """
def run(playwright):
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.get_by_role("spinbutton", name="Picked Quantity").click()
    page.get_by_role("spinbutton", name="Picked Quantity").fill("1")
    browser.close()
"""

    optimized = optimize(parse_script(script))

    assert [action.type for action in optimized] == [
        "setup_browser",
        "setup_context",
        "setup_page",
        "click",
        "fill",
        "close_browser",
    ]
    assert optimized[3].role == "spinbutton"
    assert optimized[3].name == "Picked Quantity"
    assert optimized[4].role == "spinbutton"
    assert optimized[4].name == "Picked Quantity"
    assert optimized[4].value == "1"


def test_optimize_does_not_merge_plain_nav_link_with_following_text_click() -> None:
    script = """
def run(playwright):
    browser = playwright.chromium.launch(headless=False)
    page = browser.new_page()
    page.get_by_role("link", name="Home", exact=True).click()
    page.get_by_text("My Client Groups").click()
    browser.close()
"""

    optimized = optimize(parse_script(script))

    assert [action.type for action in optimized] == [
        "setup_browser",
        "setup_page",
        "click",
        "click",
        "close_browser",
    ]
    assert optimized[2].name == "Home"
    assert optimized[3].name == "My Client Groups"


def test_optimize_merges_menu_like_link_with_following_text_click() -> None:
    script = """
def run(playwright):
    browser = playwright.chromium.launch(headless=False)
    page = browser.new_page()
    page.get_by_role("link", name="Actions", exact=True).click()
    page.get_by_text("Delete").click()
    browser.close()
"""

    optimized = optimize(parse_script(script))

    assert [action.type for action in optimized] == [
        "setup_browser",
        "setup_page",
        "adf_menu_select",
        "close_browser",
    ]
    assert optimized[2].name == "Actions"
    assert optimized[2].value == "Delete"


def test_optimize_merges_oracle_compact_text_menu_trigger_with_following_text_click() -> None:
    script = """
def run(playwright):
    browser = playwright.chromium.launch(headless=False)
    page = browser.new_page()
    page.get_by_text("ActionsValidateReprice").click()
    page.get_by_text("Edit Additional Information").click()
    browser.close()
"""

    optimized = optimize(parse_script(script))

    assert [action.type for action in optimized] == [
        "setup_browser",
        "setup_page",
        "adf_menu_select",
        "close_browser",
    ]
    assert optimized[2].name == "ActionsValidateReprice"
    assert optimized[2].value == "Edit Additional Information"
    assert optimized[2].action_kwargs == {
        "trigger_kind": "text",
        "option_name": "Edit Additional Information",
        "option_exact": None,
    }


def test_optimize_preserves_exact_day_match_for_date_pick() -> None:
    script = """
def run(playwright):
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.get_by_title("Select Date.").click()
    page.get_by_role("button", name="3", exact=True).click()
    browser.close()
"""

    optimized = optimize(parse_script(script))

    date_pick = next(action for action in optimized if action.type == "date_pick")

    assert date_pick.action_kwargs["day_label"] == "3"
    assert date_pick.action_kwargs["day_role"] == "button"
    assert date_pick.action_kwargs["day_exact"] is True


def test_optimize_drops_leading_timeout_wait_before_first_goto() -> None:
    script = """
def run(playwright):
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.wait_for_timeout(180000)
    page.goto("https://example.test/login")
    browser.close()
"""

    optimized = optimize(parse_script(script))

    assert [action.type for action in optimized] == [
        "setup_browser",
        "setup_context",
        "setup_page",
        "goto",
        "close_browser",
    ]
    assert optimized[3].url == "https://example.test/login"


def test_optimize_merges_lov_textbox_trigger_with_following_gridcell_click() -> None:
    """An Oracle ADF LOV input is exposed as role="textbox"; [click the LOV input] +
    [click the value gridcell] must merge into a single select_combobox so it gets the
    value-equality postcondition instead of a flaky standalone gridcell click."""
    script = """
def run(playwright):
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.get_by_role("textbox", name="Business Unit").click()
    page.get_by_role("gridcell", name="850 Miter Shared Services BU").click()
    browser.close()
"""

    optimized = optimize(parse_script(script))

    merged = [a for a in optimized if a.type == "select_combobox"]
    assert len(merged) == 1
    assert merged[0].name == "Business Unit"  # LOV field label (trigger)
    assert merged[0].value == "850 Miter Shared Services BU"  # picked value
    assert merged[0].action_kwargs.get("option_name") == "850 Miter Shared Services BU"
    # the standalone gridcell click must be gone (consumed by the merge)
    assert not any(a.type == "click" and a.role == "gridcell" for a in optimized)


def test_optimize_does_not_merge_textbox_click_followed_by_fill() -> None:
    """Guard against over-merging: an ordinary textbox click + fill (e.g. a login field)
    must NOT become select_combobox -- only a NAMED option/cell/gridcell click triggers it."""
    script = """
def run(playwright):
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.get_by_role("textbox", name="Username").click()
    page.get_by_role("textbox", name="Username").fill("FUSDEV.CNV")
    browser.close()
"""

    optimized = optimize(parse_script(script))

    assert not any(a.type == "select_combobox" for a in optimized)


def test_optimize_supports_header_action_followed_by_multi_line_loop() -> None:
    script = """
import re

def run(playwright):
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.get_by_role("textbox", name="Accounting Date").fill("12/31/25")
    for index, line in enumerate(multi_line, start=1):
        page.get_by_role("row", name=re.compile(rf"^{index}\\b")).get_by_label("Description").fill(line["line_description"])
    browser.close()
"""

    optimized = optimize(parse_script(script))

    assert any(isinstance(action, MultiLineLoop) for action in optimized)
    assert any(getattr(action, "type", None) == "fill" for action in optimized)
