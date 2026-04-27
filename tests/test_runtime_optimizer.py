from src.runtime.optimizer import optimize
from src.runtime.parser import parse_script


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
