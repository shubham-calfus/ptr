import pytest

from src.runtime.parser import ParseCoverageError, parse_script
from src.runtime.script_generator import CoverageError, generate_full_script
from src.tools.tools import _prepare_script_via_ast


def _full_recording(body: str) -> str:
    return f"""
from playwright.sync_api import Playwright, sync_playwright


def run(playwright: Playwright) -> None:
{body}


with sync_playwright() as playwright:
    run(playwright)
"""


def test_parse_script_rejects_unsupported_run_statement() -> None:
    script = """
def run(playwright):
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    search_box = page.get_by_role("textbox", name="Search")
    browser.close()
"""

    with pytest.raises(ParseCoverageError) as excinfo:
        parse_script(script)

    message = str(excinfo.value)
    assert "line 6" in message
    assert 'search_box = page.get_by_role("textbox", name="Search")' in message


def test_generate_full_script_preserves_page_source_and_title_click_helper() -> None:
    script = """
def run(playwright):
    browser = playwright.chromium.launch(headless=False)
    page = browser.new_page()
    page.get_by_title("Show more actions").click()
    browser.close()
"""

    generated = generate_full_script(parse_script(script))

    assert "page = _ptr_register_page(browser.new_page())" in generated
    assert (
        "_ptr_tracked_action('click_title', 'Show more actions', "
        "_ptr_click_text_target, page.get_by_title('Show more actions'), page, 'Show more actions')"
    ) in generated


def test_prepare_script_via_ast_keeps_login_textbox_click_supported() -> None:
    script = _full_recording(
        """    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.get_by_role("textbox", name="Username").click()
    page.get_by_role("textbox", name="Username").fill("svc.user")
    browser.close()"""
    )

    prepared = _prepare_script_via_ast(script)

    assert (
        "_ptr_tracked_action('click_textbox', 'Username', _ptr_raw_click, "
        "page.get_by_role('textbox', name='Username'), page, 'Username')"
    ) in prepared
    assert (
        "_ptr_tracked_action('fill_textbox', 'Username', _ptr_raw_fill, "
        "page.get_by_role('textbox', name='Username'), page, 'Username', 'svc.user')"
    ) in prepared
    assert "Recording contains actions the AST runner does not safely support yet." not in prepared


def test_prepare_script_via_ast_tracks_goto_and_press_actions() -> None:
    script = _full_recording(
        """    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://example.com")
    page.get_by_role("textbox", name="Password").press("Enter")
    browser.close()"""
    )

    prepared = _prepare_script_via_ast(script)

    assert (
        "_ptr_tracked_action('goto', 'https://example.com', _ptr_goto_page, "
        "page, 'https://example.com')"
    ) in prepared
    assert (
        "_ptr_tracked_action('press_key', 'Password', _ptr_raw_press, "
        "page.get_by_role('textbox', name='Password'), page, 'Password', 'Enter')"
    ) in prepared


def test_generate_full_script_rejects_unsafe_generic_locator_click() -> None:
    script = """
def run(playwright):
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.locator(".mystery-target").click()
    browser.close()
"""

    actions = parse_script(script)

    with pytest.raises(CoverageError) as excinfo:
        generate_full_script(actions)

    message = str(excinfo.value)
    assert "line 6" in message
    assert "Click target does not map to a resilient helper" in message
    assert 'page.locator(".mystery-target").click()' in message


def test_prepare_script_via_ast_fails_fast_for_raw_select_option_gap() -> None:
    script = _full_recording(
        """    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.locator("select").select_option("Approved")
    browser.close()"""
    )

    with pytest.raises(RuntimeError) as excinfo:
        _prepare_script_via_ast(script)

    message = str(excinfo.value)
    assert "does not safely support yet" in message
    assert 'Action "select_option" still relies on a raw Playwright call.' in message


def test_generate_full_script_supports_named_secondary_pages() -> None:
    script = """
def run(playwright):
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    review_page = context.new_page()
    review_page.go_forward()
    browser.close()
"""

    generated = generate_full_script(parse_script(script))

    assert "review_page = _ptr_register_page(context.new_page())" in generated
    assert "review_page.go_forward()" in generated


def test_prepare_script_via_ast_keeps_home_navigation_and_followup_click_separate() -> None:
    script = _full_recording(
        """    browser = playwright.chromium.launch(headless=False)
    page = browser.new_page()
    page.get_by_role("link", name="Home", exact=True).click()
    page.get_by_text("My Client Groups").click()
    browser.close()"""
    )

    prepared = _prepare_script_via_ast(script)

    assert (
        "_ptr_tracked_action('click_link', 'Home', "
        "_ptr_click_text_target, page.get_by_role('link', name='Home', exact=True), page, 'Home')"
    ) in prepared
    assert (
        "_ptr_tracked_action('click_text', 'My Client Groups', "
        "_ptr_click_text_target, page.get_by_text('My Client Groups'), page, 'My Client Groups')"
    ) in prepared
    assert "_ptr_tracked_action('adf_menu_select', 'Home'" not in prepared
