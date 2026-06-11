import pytest

from src.runtime import helpers_v2
from src.runtime.parser import ParseCoverageError, parse_script
from src.runtime.script_generator import generate_full_script
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


def test_generate_full_script_inlines_raw_fallback_for_unsafe_generic_locator_click() -> None:
    script = """
def run(playwright):
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.locator(".mystery-target").click()
    browser.close()
"""

    generated = generate_full_script(parse_script(script))

    assert "_ptr_tracked_raw_action('click', '.mystery-target'" in generated
    assert "raw_inline_reason" in generated
    assert "Click target does not map to a resilient helper" in generated
    assert 'page.locator(".mystery-target").click()' in generated


def test_prepare_script_via_ast_keeps_generic_locator_click_inline_in_ast_path() -> None:
    script = _full_recording(
        """    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.locator(".mystery-target").click()
    browser.close()"""
    )

    prepared = _prepare_script_via_ast(script)

    assert "_ptr_tracked_raw_action('click', '.mystery-target'" in prepared
    assert "Recording contains actions the AST runner does not safely support yet." not in prepared


def test_prepare_script_via_ast_supports_select_option_actions() -> None:
    script = _full_recording(
        """    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.get_by_label("Type").select_option("3")
    browser.close()"""
    )

    prepared = _prepare_script_via_ast(script)

    assert (
        "_ptr_tracked_action('select_option', 'Type', _ptr_select_option_target, "
        "page.get_by_label('Type'), page, 'Type', ['3'], {})"
    ) in prepared
    assert "Recording contains actions the AST runner does not safely support yet." not in prepared


def test_prepare_script_via_ast_supports_generic_locator_select_option_actions() -> None:
    script = _full_recording(
        """    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.locator("select").select_option("Approved")
    browser.close()"""
    )

    prepared = _prepare_script_via_ast(script)

    assert (
        "_ptr_tracked_action('select_option', 'select', _ptr_select_option_target, "
        "page.locator('select'), page, 'select', ['Approved'], {})"
    ) in prepared
    assert "Recording contains actions the AST runner does not safely support yet." not in prepared


def test_prepare_script_via_ast_supports_checkbox_check_actions() -> None:
    script = _full_recording(
        """    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.get_by_role("checkbox", name="Create a job application on").check()
    browser.close()"""
    )

    prepared = _prepare_script_via_ast(script)

    assert (
        "_ptr_tracked_action('check', 'Create a job application on', _ptr_check_target, "
        "page.get_by_role('checkbox', name='Create a job application on'), page, 'Create a job application on')"
    ) in prepared
    assert "Recording contains actions the AST runner does not safely support yet." not in prepared


def test_prepare_script_via_ast_uses_inner_label_for_nested_fill_actions() -> None:
    script = _full_recording(
        """    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.get_by_role("row", name="1 Item Type Amount").get_by_label("Amount").fill("-10")
    browser.close()"""
    )

    prepared = _prepare_script_via_ast(script)

    assert (
        "_ptr_tracked_action('fill_textbox', 'Amount', _ptr_fill_textbox, "
        "page.get_by_role('row', name='1 Item Type Amount').get_by_label('Amount'), page, 'Amount', '-10')"
    ) in prepared


def test_prepare_script_via_ast_supports_text_dblclick_actions() -> None:
    script = _full_recording(
        """    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.get_by_text("10008").dblclick()
    browser.close()"""
    )

    prepared = _prepare_script_via_ast(script)

    assert (
        "_ptr_tracked_action('dblclick_text', '10008', _ptr_dblclick_text_target, "
        "page.get_by_text('10008'), page, '10008')"
    ) in prepared
    assert "Recording contains actions the AST runner does not safely support yet." not in prepared


def test_prepare_script_via_ast_supports_table_scoped_label_click_actions() -> None:
    script = _full_recording(
        """    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.get_by_role("table", name="Invoice Lines").get_by_label("Amount").click()
    browser.close()"""
    )

    prepared = _prepare_script_via_ast(script)

    assert (
        "_ptr_tracked_action('click_table_field', 'Amount', _ptr_click_table_field, "
        "page.get_by_role('table', name='Invoice Lines').get_by_label('Amount'), page, 'Amount')"
    ) in prepared
    assert "Recording contains actions the AST runner does not safely support yet." not in prepared


def test_generate_full_script_inlines_raw_fallback_for_non_text_dblclick_actions() -> None:
    script = """
def run(playwright):
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.get_by_role("button", name="Open").dblclick()
    browser.close()
"""

    generated = generate_full_script(parse_script(script))

    assert "_ptr_tracked_raw_action('dblclick', 'Open'" in generated
    assert "raw_inline_reason" in generated
    assert "Double-click target does not map to a resilient helper" in generated
    assert 'page.get_by_role("button", name="Open").dblclick()' in generated


def test_helpers_v2_exports_new_ast_click_helpers() -> None:
    assert "_ptr_dblclick_text_target" in helpers_v2.__all__
    assert "_ptr_click_table_field" in helpers_v2.__all__
    assert "_ptr_tracked_raw_action" in helpers_v2.__all__


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


def test_prepare_script_via_ast_supports_role_row_clicks() -> None:
    script = _full_recording(
        """    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.get_by_role("row", name="Academic").click()
    browser.close()"""
    )

    prepared = _prepare_script_via_ast(script)

    assert (
        "_ptr_tracked_action('click_row', 'Academic', "
        "_ptr_click_table_row, page.get_by_role('row', name='Academic'), page, 'Academic')"
    ) in prepared
    assert "Recording contains actions the AST runner does not safely support yet." not in prepared


def test_prepare_script_via_ast_maps_row_scoped_label_clicks_to_table_field_helper() -> None:
    script = _full_recording(
        """    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.get_by_role("row", name="1 Item Type 100.00 Amount").get_by_label("Description").click()
    browser.close()"""
    )

    prepared = _prepare_script_via_ast(script)

    assert (
        "_ptr_tracked_action('click_table_field', 'Description', _ptr_click_table_field, "
        "page.get_by_role('row', name='1 Item Type 100.00 Amount').get_by_label('Description'), page, 'Description')"
    ) in prepared
    assert "_ptr_tracked_action('click_row', '1 Item Type 100.00 Amount'" not in prepared
    assert "Recording contains actions the AST runner does not safely support yet." not in prepared


def test_prepare_script_via_ast_preserves_exact_day_match_for_date_pick() -> None:
    script = _full_recording(
        """    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.get_by_title("Select Date.").click()
    page.get_by_role("button", name="3", exact=True).click()
    browser.close()"""
    )

    prepared = _prepare_script_via_ast(script)

    assert (
        "_ptr_tracked_action('date_pick', 'Select Date.', _ptr_pick_date_via_icon, "
        "page.get_by_title('Select Date.'), page.get_by_role('button', name='3', exact=True), "
        "page, 'Select Date.', '3')"
    ) in prepared
