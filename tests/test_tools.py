from src.tools.tools import (
    _derive_parameters_file_candidates,
    _expand_recordings_for_parameter_rows_data,
    _inject_network_idle_waits,
    _inject_runtime_helpers,
    _parse_excel_parameter_sets,
    _parse_excel_parameters,
    _parameterise_script,
    _parameters_to_json_object,
    _rewrite_post_login_goto_calls,
    _rewrite_adf_menu_panel_selection_calls,
    _rewrite_combobox_click_calls,
    _rewrite_combobox_selection_calls,
    _rewrite_exact_button_click_calls,
    _rewrite_date_picker_click_calls,
    _rewrite_exact_text_click_calls,
    _rewrite_navigation_button_click_calls,
    _rewrite_search_popup_selection_calls,
    _rewrite_textbox_click_calls,
    _rewrite_textbox_fill_calls,
    _split_storage_object_ref,
    _strip_redundant_textbox_focus_clicks,
)


def test_inject_runtime_helpers_checks_playwright_install_failures() -> None:
    script = """
from playwright.sync_api import sync_playwright


def run(playwright):
    browser = playwright.chromium.launch(headless=False)
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
"""

    instrumented = _inject_runtime_helpers(script)

    assert "check=True" in instrumented
    assert "capture_output=True" in instrumented
    assert "timed out after 180 seconds" in instrumented
    assert "Chromium is not installed and automatic " in instrumented
    assert "`playwright install chromium` failed:" in instrumented


def test_inject_runtime_helpers_supports_steel_browser_connection() -> None:
    script = """
from playwright.sync_api import sync_playwright


def run(playwright):
    browser = playwright.chromium.launch(headless=False, timeout=1234)
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
"""

    instrumented = _inject_runtime_helpers(script)

    assert "connect_over_cdp" in instrumented
    assert "from steel import Steel" in instrumented
    assert "sessions.create" in instrumented
    assert "sessionId" in instrumented
    assert "apiKey" in instrumented
    assert "STEEL_CONNECT_URL" in instrumented
    assert "sessions.release" in instrumented
    assert "PTR_BROWSER_PROVIDER" in instrumented
    assert "PTR_WINDOW_WIDTH" in instrumented
    assert "PTR_WINDOW_HEIGHT" in instrumented
    assert "PTR_VIEWPORT_WIDTH_MARGIN" in instrumented
    assert "PTR_VIEWPORT_HEIGHT_MARGIN" in instrumented
    assert "def _ptr_window_dimensions()" in instrumented
    assert "def _ptr_target_viewport()" in instrumented
    assert "PTR_CAPTURE_STEPS" in instrumented
    assert "PTR_RECORD_VIDEO" in instrumented
    assert "PTR_STEP_SCREENSHOT_FULL_PAGE" in instrumented
    assert "def _ptr_wait_ms" in instrumented
    assert "STEEL_API_KEY" in instrumented
    assert "STEEL_SESSION_ID" in instrumented
    assert "PTR_STEEL_CONNECT_RETRIES" in instrumented
    assert "PTR_STEEL_SESSION_TIMEOUT_MS" in instrumented
    assert "PTR_GOTO_RETRIES" in instrumented
    assert "ERR_NAME_NOT_RESOLVED" in instrumented
    assert "ERR_ABORTED" in instrumented
    assert "_ptr_is_recoverable_aborted_navigation" in instrumented
    assert "_ptr_wait_for_post_login_redirect" in instrumented
    assert "PTR_LOGIN_REDIRECT_WAIT_MS" in instrumented
    assert "_ptr_same_origin" in instrumented
    assert "urlparse" in instrumented
    assert "--start-maximized" in instrumented
    assert "--start-fullscreen" in instrumented
    assert 'kwargs["viewport"] = {' in instrumented
    assert "Unsupported PTR_BROWSER_PROVIDER" in instrumented


def test_strip_redundant_textbox_focus_clicks_keeps_navigation_focus_step() -> None:
    script = """
page.get_by_role("textbox", name="Notes").click()
page.get_by_role("button", name="Continue").click()
page.get_by_role("textbox", name="Username").click()
page.get_by_role("textbox", name="Username").fill("demo")
page.get_by_role("textbox", name="Username").press("Tab")
page.get_by_role("textbox", name="Password").fill("secret")
page.get_by_role("textbox", name="Search").click()
page.get_by_role("cell", name="Performance").click()
"""

    rewritten = _strip_redundant_textbox_focus_clicks(script)

    assert 'page.get_by_role("textbox", name="Notes").click()' in rewritten
    assert 'page.get_by_role("textbox", name="Username").click()' not in rewritten
    assert 'page.get_by_role("textbox", name="Username").press("Tab")' not in rewritten
    assert 'page.get_by_role("textbox", name="Search").click()' in rewritten


def test_rewrite_textbox_click_calls_wraps_notes_click_with_helper() -> None:
    script = """
page.get_by_role("textbox", name="Notes").click()
page.get_by_role("textbox", name="Optional", exact=False).click()
"""

    rewritten = _rewrite_textbox_click_calls(script)

    assert '_ptr_click_textbox(page.get_by_role("textbox", name="Notes"), page, "Notes")' in rewritten
    assert 'page.get_by_role("textbox", name="Optional", exact=False).click()' in rewritten


def test_rewrite_textbox_fill_calls_wraps_fill_with_fallback_helper() -> None:
    script = """
page.get_by_role("textbox", name="Salary Amount EUR Annually").fill("47,575")
page.get_by_role("textbox", name="Notes").fill("done")
page.get_by_role("textbox", name="Username").press("Tab")
"""

    rewritten = _rewrite_textbox_fill_calls(script)

    assert '_ptr_fill_textbox(page.get_by_role("textbox", name="Salary Amount EUR Annually"), page, "Salary Amount EUR Annually", "47,575")' in rewritten
    assert '_ptr_fill_textbox(page.get_by_role("textbox", name="Notes"), page, "Notes", "done")' in rewritten
    assert 'page.get_by_role("textbox", name="Username").press("Tab")' in rewritten


def test_rewrite_exact_text_click_calls_wraps_single_word_exact_text_only() -> None:
    script = """
page.get_by_text("Comments", exact=True).click()
page.get_by_text("Post to Ledger").click()
page.get_by_text("Complete and Review", exact=True).click()
page.get_by_text("Optional", exact=False).click()
"""

    rewritten = _rewrite_exact_text_click_calls(script)

    assert '_ptr_click_text_target(page, "Comments")' in rewritten
    assert 'page.get_by_text("Post to Ledger").click()' in rewritten
    assert 'page.get_by_text("Complete and Review", exact=True).click()' in rewritten
    assert 'page.get_by_text("Optional", exact=False).click()' in rewritten


def test_rewrite_exact_button_click_calls_wraps_popup_button_with_helper() -> None:
    script = """
page.get_by_role("button", name="View Accounting").click()
page.get_by_role("button", name="Search").click()
page.get_by_role("button", name="Save").click()
page.get_by_role("button", name="Done").click()
page.get_by_role("button", name="Continue").click()
page.get_by_role("button", name="15").click()
page.get_by_role("button", name="Optional", exact=False).click()
"""

    rewritten = _rewrite_exact_button_click_calls(script)

    assert '_ptr_click_button_target(page, "View Accounting")' in rewritten
    assert '_ptr_click_button_target(page, "Search")' in rewritten
    assert '_ptr_click_button_target(page, "Save")' in rewritten
    assert '_ptr_click_button_target(page, "Done")' in rewritten
    assert 'page.get_by_role("button", name="Continue").click()' in rewritten
    assert 'page.get_by_role("button", name="15").click()' in rewritten
    assert 'page.get_by_role("button", name="Optional", exact=False).click()' in rewritten


def test_rewrite_search_popup_selection_calls_wraps_search_icon_and_text_pick() -> None:
    script = """
page.get_by_title("Search: Receipt Method").click()
page.get_by_text("MMA Account Receipt.").click()
page.get_by_title("Search: Site").click()
page.get_by_text("No.5 Circuit Street Light").click()
page.get_by_title("Search: Transaction Type").click()
page.get_by_role("cell", name="YEU Prepayment").first.click()
"""

    rewritten = _rewrite_search_popup_selection_calls(script)

    assert (
        '_ptr_select_search_trigger_option(page, "Search: Receipt Method", "MMA Account Receipt.", '
        'option_kind="text", option_exact=False)'
    ) in rewritten
    assert (
        '_ptr_select_search_trigger_option(page, "Search: Site", "No.5 Circuit Street Light", '
        'option_kind="text", option_exact=False)'
    ) in rewritten
    assert (
        '_ptr_select_search_trigger_option(page, "Search: Transaction Type", "YEU Prepayment", '
        'option_kind="cell", option_exact=False)'
    ) in rewritten
    assert 'page.get_by_text("MMA Account Receipt.").click()' not in rewritten
    assert 'page.get_by_text("No.5 Circuit Street Light").click()' not in rewritten
    assert 'page.get_by_role("cell", name="YEU Prepayment").first.click()' not in rewritten


def test_rewrite_adf_menu_panel_selection_calls_wraps_title_and_link_trigger_pairs() -> None:
    script = """
page.get_by_title("Complete and Create Another").click()
page.get_by_text("Complete and Review").click()
page.get_by_title("Submit and Create Another").click()
page.get_by_text("Submit", exact=True).click()
page.get_by_role("link", name="Actions", exact=True).click()
page.get_by_text("Post to Ledger").click()
page.get_by_title("Search: Transaction Type").click()
page.get_by_text("Manual").click()
page.get_by_title("Select Date.").click()
page.get_by_text("15").click()
"""

    rewritten = _rewrite_adf_menu_panel_selection_calls(script)

    assert (
        '_ptr_select_adf_menu_panel_option(page, "Complete and Create Another", "Complete and Review", trigger_kind="title")'
        in rewritten
    )
    assert (
        '_ptr_select_adf_menu_panel_option(page, "Submit and Create Another", "Submit", trigger_kind="title")'
        in rewritten
    )
    assert (
        '_ptr_select_adf_menu_panel_option(page, "Actions", "Post to Ledger", trigger_kind="link")'
        in rewritten
    )
    assert 'page.get_by_title("Search: Transaction Type").click()' in rewritten
    assert 'page.get_by_text("Manual").click()' in rewritten
    assert 'page.get_by_title("Select Date.").click()' in rewritten
    assert 'page.get_by_text("15").click()' in rewritten


def test_rewrite_combobox_selection_calls_wraps_combobox_option_pair() -> None:
    script = """
page.get_by_role("combobox", name="What's the way to change the assignment?").click()
page.get_by_role("cell", name="Temporary Assignment").click()
"""

    rewritten = _rewrite_combobox_selection_calls(script)

    assert (
        '_ptr_select_combobox_option(page, "What\'s the way to change the assignment?", "Temporary Assignment")'
        in rewritten
    )
    assert 'page.get_by_role("cell", name="Temporary Assignment").click()' not in rewritten


def test_rewrite_combobox_selection_calls_wraps_locator_arrow_and_gridcell_first_pair() -> None:
    script = """
page.get_by_role("combobox", name="Business Unit").locator("a").click()
page.get_by_role("gridcell", name="Test Solutions").first.click()
"""

    rewritten = _rewrite_combobox_selection_calls(script)

    assert '_ptr_select_combobox_option(page, "Business Unit", "Test Solutions")' in rewritten
    assert 'page.get_by_role("gridcell", name="Test Solutions").first.click()' not in rewritten


def test_rewrite_combobox_click_calls_wraps_standalone_combobox_click() -> None:
    script = """
page.get_by_role("combobox", name="Salary Basis").click()
page.get_by_role("combobox", name="Optional", exact=False).click()
"""

    rewritten = _rewrite_combobox_click_calls(script)

    assert '_ptr_click_combobox(page, "Salary Basis")' in rewritten
    assert 'page.get_by_role("combobox", name="Optional", exact=False).click()' in rewritten


def test_rewrite_combobox_click_calls_wraps_locator_arrow_standalone_click() -> None:
    script = """
page.get_by_role("combobox", name="Business Unit").locator("a").click()
"""

    rewritten = _rewrite_combobox_click_calls(script)

    assert '_ptr_click_combobox(page, "Business Unit")' in rewritten


def test_rewrite_date_picker_click_calls_wraps_calendar_icon_and_day_button() -> None:
    script = """
page.get_by_title("Select Date.").click()
page.get_by_role("button", name="15").click()
"""

    rewritten = _rewrite_date_picker_click_calls(script)

    assert '_ptr_pick_date_via_icon(page, "Select Date.", "15")' in rewritten
    assert 'page.get_by_role("button", name="15").click()' not in rewritten


def test_rewrite_date_picker_click_calls_wraps_calendar_icon_and_day_gridcell() -> None:
    script = """
page.get_by_title("Select Date.").click()
page.get_by_role("gridcell", name="9").click()
"""

    rewritten = _rewrite_date_picker_click_calls(script)

    assert '_ptr_pick_date_via_icon(page, "Select Date.", "9")' in rewritten
    assert 'page.get_by_role("gridcell", name="9").click()' not in rewritten


def test_rewrite_navigation_button_click_calls_wraps_continue_click() -> None:
    script = """
page.get_by_role("button", name="Continue").click()
page.get_by_role("button", name="Save").click()
page.get_by_role("button", name="Optional", exact=False).click()
"""

    rewritten = _rewrite_navigation_button_click_calls(script)

    assert '_ptr_click_navigation_button(page, "Continue")' in rewritten
    assert 'page.get_by_role("button", name="Save").click()' in rewritten
    assert 'page.get_by_role("button", name="Optional", exact=False).click()' in rewritten


def test_rewrite_navigation_button_click_calls_wraps_go_back_click() -> None:
    script = """
page.get_by_role("button", name="Go back").click()
"""

    rewritten = _rewrite_navigation_button_click_calls(script)

    assert '_ptr_click_navigation_button(page, "Go back")' in rewritten


def test_rewrite_post_login_goto_calls_replaces_redundant_goto_after_password_submit() -> None:
    script = """
page.get_by_role("textbox", name="Password").press("Enter")
page.goto("https://iamoqy-test.fa.ocs.oraclecloud.com/")
page.get_by_role("link", name="Home", exact=True).click()
"""

    rewritten = _rewrite_post_login_goto_calls(script)

    assert 'page.get_by_role("textbox", name="Password").press("Enter")' in rewritten
    assert '_ptr_wait_for_post_login_redirect(page, "https://iamoqy-test.fa.ocs.oraclecloud.com/")' in rewritten
    assert 'page.goto("https://iamoqy-test.fa.ocs.oraclecloud.com/")' not in rewritten


def test_parameterise_script_does_not_reuse_stale_textbox_context_for_calendar_gridcell() -> None:
    script = """
page.get_by_role("textbox", name="Password").fill("1234567890")
page.get_by_role("textbox", name="Password").press("Enter")
page.get_by_title("Select Date.").click()
page.get_by_role("gridcell", name="9").click()
"""

    parameterised_script, default_params = _parameterise_script(script)
    gridcell_line = next(
        line for line in parameterised_script.splitlines() if 'get_by_role("gridcell"' in line
    )

    assert default_params["password"] == "1234567890"
    assert 'name="9"' in gridcell_line
    assert "{{password}}" not in gridcell_line


def test_parameterise_script_keeps_immediate_combobox_context_for_gridcell_selection() -> None:
    script = """
page.get_by_role("combobox", name="What's the way to change the assignment?").click()
page.get_by_role("gridcell", name="Temporary Assignment").click()
"""

    parameterised_script, default_params = _parameterise_script(script)

    assert default_params["what_s_the_way_to_change_the_assignment"] == "Temporary Assignment"
    assert (
        'page.get_by_role("gridcell", name="{{what_s_the_way_to_change_the_assignment}}").click()'
        in parameterised_script
    )


def test_parameterise_script_does_not_treat_existing_placeholders_as_defaults() -> None:
    script = """
page.goto("{{url}}")
page.get_by_role("textbox", name="Username").fill("{{username}}")
"""

    parameterised_script, default_params = _parameterise_script(script)

    assert 'page.goto("{{url}}")' in parameterised_script
    assert 'page.get_by_role("textbox", name="Username").fill("{{username}}")' in parameterised_script
    assert default_params == {}


def test_parse_excel_parameters_supports_headerless_sheet() -> None:
    from io import BytesIO

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["url", "https://example.com/login"])
    ws.append(["username", "demo@example.com"])
    ws.append(["click_save", "ignored"])

    buffer = BytesIO()
    wb.save(buffer)
    wb.close()

    params = _parse_excel_parameters(buffer.getvalue())

    assert params == {
        "url": "https://example.com/login",
        "username": "demo@example.com",
    }


def test_parse_excel_parameters_supports_horizontal_header_value_sheet() -> None:
    from io import BytesIO

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(
        [
            "startUrl",
            "Username",
            "Password",
            "Business Unit",
            "Receipt Number",
        ]
    )
    ws.append(
        [
            "https://iamoqy-test.fa.ocs.oraclecloud.com/",
            "svc",
            "Calfus@123",
            "Test Solutions",
            "RN-465346",
        ]
    )

    buffer = BytesIO()
    wb.save(buffer)
    wb.close()

    params = _parse_excel_parameters(buffer.getvalue())

    assert params == {
        "url": "https://iamoqy-test.fa.ocs.oraclecloud.com/",
        "username": "svc",
        "password": "Calfus@123",
        "business_unit": "Test Solutions",
        "receipt_number": "RN-465346",
    }


def test_parse_excel_parameter_sets_supports_multiple_horizontal_data_rows() -> None:
    from io import BytesIO

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(
        [
            "startUrl",
            "Username",
            "Password",
            "Receipt Number",
            "Entered Amount",
        ]
    )
    ws.append(
        [
            "https://iamoqy-test.fa.ocs.oraclecloud.com/",
            "svc",
            "Calfus@123",
            "RN-46534434",
            "55",
        ]
    )
    ws.append(
        [
            "https://iamoqy-test.fa.ocs.oraclecloud.com/",
            "svc",
            "Calfus@124",
            "RN-46534734",
            "56",
        ]
    )

    buffer = BytesIO()
    wb.save(buffer)
    wb.close()

    parameter_sets = _parse_excel_parameter_sets(buffer.getvalue())

    assert parameter_sets == [
        {
            "row_index": 2,
            "values": {
                "url": "https://iamoqy-test.fa.ocs.oraclecloud.com/",
                "username": "svc",
                "password": "Calfus@123",
                "receipt_number": "RN-46534434",
                "entered_amount": "55",
            },
        },
        {
            "row_index": 3,
            "values": {
                "url": "https://iamoqy-test.fa.ocs.oraclecloud.com/",
                "username": "svc",
                "password": "Calfus@124",
                "receipt_number": "RN-46534734",
                "entered_amount": "56",
            },
        },
    ]


def test_expand_recordings_for_parameter_rows_data_fans_out_multiple_excel_rows(monkeypatch) -> None:
    def _fake_load_recording_parameter_sets(recording, file_key):
        assert recording["file"] == "recordings/demo.py"
        assert file_key == "recordings/demo.py"
        return (
            [
                {
                    "row_index": 2,
                    "values": {
                        "username": "svc",
                        "password": "Calfus@123",
                        "receipt_number": "RN-46534434",
                    },
                },
                {
                    "row_index": 3,
                    "values": {
                        "username": "svc",
                        "password": "Calfus@124",
                        "receipt_number": "RN-46534734",
                    },
                },
            ],
            "recordings/demo_params.xlsx",
        )

    monkeypatch.setattr(
        "src.tools.tools._load_recording_parameter_sets",
        _fake_load_recording_parameter_sets,
    )

    expanded = _expand_recordings_for_parameter_rows_data(
        [
            {
                "id": "rec-1",
                "name": "fake_2",
                "file": "recordings/demo.py",
                "parameters": {
                    "entered_amount": "55",
                },
            }
        ]
    )

    assert len(expanded) == 2
    assert expanded[0]["id"] == "rec-1-row-2"
    assert expanded[0]["name"] == "fake_2 [row 2]"
    assert expanded[0]["parameters"] == {
        "username": "svc",
        "password": "Calfus@123",
        "receipt_number": "RN-46534434",
        "entered_amount": "55",
    }
    assert expanded[0]["parameters_file_key"] == "recordings/demo_params.xlsx"
    assert expanded[0]["parameter_set_index"] == 1
    assert expanded[0]["parameter_row_index"] == 2
    assert expanded[0]["skip_parameters_file_load"] is True

    assert expanded[1]["id"] == "rec-1-row-3"
    assert expanded[1]["name"] == "fake_2 [row 3]"
    assert expanded[1]["parameters"] == {
        "username": "svc",
        "password": "Calfus@124",
        "receipt_number": "RN-46534734",
        "entered_amount": "55",
    }
    assert expanded[1]["parameters_file_key"] == "recordings/demo_params.xlsx"
    assert expanded[1]["parameter_set_index"] == 2
    assert expanded[1]["parameter_row_index"] == 3
    assert expanded[1]["skip_parameters_file_load"] is True


def test_parameters_to_json_object_normalizes_inline_parameter_keys() -> None:
    params = _parameters_to_json_object(
        {
            "startUrl": "https://iamoqy-test.fa.ocs.oraclecloud.com/",
            "Username": "svc",
            "Receipt Number": "RN-465346",
            "ignored_blank": "   ",
            "none_value": None,
        }
    )

    assert params == {
        "url": "https://iamoqy-test.fa.ocs.oraclecloud.com/",
        "username": "svc",
        "receipt_number": "RN-465346",
    }


def test_split_storage_object_ref_strips_current_bucket_prefix(monkeypatch) -> None:
    monkeypatch.setenv("STORAGE_ACTIVITIES_BUCKET", "local-dev-bucket")

    bucket_name, object_key = _split_storage_object_ref(
        "local-dev-bucket/recordings/8279897e-21b5-4781-ab82-fd4bd0095355/fake_2_params.xlsx"
    )

    assert bucket_name == "local-dev-bucket"
    assert object_key == "recordings/8279897e-21b5-4781-ab82-fd4bd0095355/fake_2_params.xlsx"


def test_derive_parameters_file_candidates_uses_sibling_params_file(monkeypatch) -> None:
    monkeypatch.setenv("STORAGE_ACTIVITIES_BUCKET", "local-dev-bucket")

    candidates = _derive_parameters_file_candidates(
        "local-dev-bucket/recordings/8279897e-21b5-4781-ab82-fd4bd0095355/fake_2.py"
    )

    assert candidates == [
        "local-dev-bucket/recordings/8279897e-21b5-4781-ab82-fd4bd0095355/fake_2_params.xlsx",
        "recordings/8279897e-21b5-4781-ab82-fd4bd0095355/fake_2_params.xlsx",
        "local-dev-bucket/recordings/8279897e-21b5-4781-ab82-fd4bd0095355/fake_2_params.csv",
        "recordings/8279897e-21b5-4781-ab82-fd4bd0095355/fake_2_params.csv",
    ]


def test_inject_network_idle_waits_adds_pause_after_navigation_buttons() -> None:
    script = """
page.get_by_role("button", name="Continue").click()
page.get_by_role("button", name="Continue").click()
page.get_by_role("textbox", name="Comments").fill("done")
"""

    rewritten = _inject_network_idle_waits(script)

    assert rewritten.count('_ptr_wait_ms("PTR_NAV_BUTTON_WAIT_MS", 2000)') == 2
    assert '_ptr_wait_ms("PTR_POST_CLICK_WAIT_MS", 1500)' not in rewritten


def test_inject_runtime_helpers_textbox_fill_helper_tries_generic_text_entry_fallbacks() -> None:
    script = """
from playwright.sync_api import sync_playwright


def run(playwright):
    page = None
    browser = playwright.chromium.launch(headless=False)
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
"""

    instrumented = _inject_runtime_helpers(script)

    assert "PTR_TEXT_ENTRY_TIMEOUT_MS" in instrumented
    assert "PTR_PRIMARY_TEXT_ENTRY_TIMEOUT_MS" in instrumented
    assert "def _ptr_fill_textbox(primary_locator, page, label: str, value, **kwargs):" in instrumented
    assert 'get_by_role("spinbutton", name=label)' in instrumented
    assert 'get_by_role("combobox", name=label)' in instrumented
    assert 'get_by_label(label, exact=True)' in instrumented
    assert 'get_by_placeholder(label, exact=False)' in instrumented
    assert 'aria-label' in instrumented
    assert 'aria-labelledby' in instrumented
    assert 'label-hint' in instrumented
    assert 'data-oj-field' in instrumented
    assert "oj-c-input-number" in instrumented
    assert 'oj-c-input-number[label-hint="{label}"] input' in instrumented
    assert "[id$='-suffix']" in instrumented
    assert 'input, textarea, [role="textbox"]' in instrumented
    assert 'label[for="${node.id}"]' in instrumented
    assert "matched_candidates.sort(reverse=True)" in instrumented


def test_inject_runtime_helpers_text_click_helper_tries_role_and_text_fallbacks() -> None:
    script = """
from playwright.sync_api import sync_playwright


def run(playwright):
    page = None
    browser = playwright.chromium.launch(headless=False)
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
"""

    instrumented = _inject_runtime_helpers(script)

    assert "PTR_TEXT_CLICK_TIMEOUT_MS" in instrumented
    assert "PTR_PRIMARY_TEXT_CLICK_TIMEOUT_MS" in instrumented
    assert "def _ptr_click_textbox(primary_locator, page, label: str, **kwargs):" in instrumented
    assert 'get_by_text(label, exact=True)' in instrumented
    assert 'get_by_role("tab", name=label, exact=True)' in instrumented
    assert 'get_by_role("button", name=label, exact=False)' in instrumented
    assert "_PTR_POPUP_SCOPE_SELECTORS" in instrumented
    assert "_ptr_get_visible_scopes" in instrumented
    assert '.af_menu_popup:visible' in instrumented
    assert '[role="menu"]:visible' in instrumented
    assert 'button, a, [role="button"], [role="tab"]' in instrumented
    assert 'all(token in haystack for token in label_tokens)' in instrumented
    assert "_ptr_resolve_active_page" in instrumented
    assert 'getattr(context, "pages", [])' in instrumented
    assert "_ptr_is_closed_target_error" in instrumented
    assert 'Unable to click text target' in instrumented
    compile(instrumented, "<instrumented>", "exec")


def test_inject_runtime_helpers_button_click_helper_prioritizes_buttons_and_dialogs() -> None:
    script = """
from playwright.sync_api import sync_playwright


def run(playwright):
    page = None
    browser = playwright.chromium.launch(headless=False)
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
"""

    instrumented = _inject_runtime_helpers(script)

    assert '_ptr_click_button_target' in instrumented
    assert 'PTR_BUTTON_CLICK_TIMEOUT_MS' in instrumented
    assert '_ptr_get_visible_scopes(current_page)' in instrumented
    assert 'scope.get_by_role("button", name=label, exact=True).first' in instrumented
    assert '[role="dialog"]:visible' in instrumented
    assert '.af_menu_popup:visible' in instrumented
    assert '[data-afr-popupid]:visible' in instrumented
    assert 'button:has-text("' in instrumented
    assert 'all(token in haystack for token in label_tokens)' in instrumented
    assert 'Unable to click button target' in instrumented
    compile(instrumented, "<instrumented>", "exec")


def test_inject_runtime_helpers_combobox_helper_tries_role_fallbacks() -> None:
    script = """
from playwright.sync_api import sync_playwright


def run(playwright):
    page = None
    browser = playwright.chromium.launch(headless=False)
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
"""

    instrumented = _inject_runtime_helpers(script)

    assert "PTR_COMBOBOX_TIMEOUT_MS" in instrumented
    assert '_ptr_select_combobox_option' in instrumented
    assert '_ptr_click_combobox' in instrumented
    assert 'get_by_role("combobox", name=label, exact=True)' in instrumented
    assert 'oj-select-single .oj-searchselect-main-field' in instrumented
    assert 'oj-select-single .oj-searchselect-arrow' in instrumented
    assert 'oj-select-single:has-text("' in instrumented
    assert 'label-hint' in instrumented
    assert 'get_by_role("cell", name=option_label, exact=True)' in instrumented
    assert 'get_by_role("gridcell", name=option_label, exact=True)' in instrumented
    assert 'Unable to select combobox option' in instrumented
    assert 'Unable to click combobox "' in instrumented
    compile(instrumented, "<instrumented>", "exec")


def test_inject_runtime_helpers_adf_menu_panel_helper_tries_codegen_trigger_first() -> None:
    script = """
from playwright.sync_api import sync_playwright


def run(playwright):
    page = None
    browser = playwright.chromium.launch(headless=False)
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
"""

    instrumented = _inject_runtime_helpers(script)

    assert "PTR_PRIMARY_ADF_MENU_PANEL_TIMEOUT_MS" in instrumented
    assert "PTR_ADF_SPLIT_BUTTON_POST_OPEN_WAIT_MS" in instrumented
    assert 'get_by_title(trigger_label, exact=True).first' in instrumented
    assert 'get_by_role("link", name=trigger_label, exact=True).first' in instrumented
    assert 'Submit and Create Another' in instrumented
    assert 'normalize-space()="Submit"' in instrumented
    assert 'newTrx::popEl' in instrumented
    assert '_ptr_try_hardcoded_split_button_option' in instrumented
    assert '_ptr_select_adf_menu_panel_option' in instrumented
    compile(instrumented, "<instrumented>", "exec")


def test_inject_runtime_helpers_date_picker_helper_targets_oj_input_date() -> None:
    script = """
from playwright.sync_api import sync_playwright


def run(playwright):
    page = None
    browser = playwright.chromium.launch(headless=False)
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
"""

    instrumented = _inject_runtime_helpers(script)

    assert "PTR_DATE_PICKER_TIMEOUT_MS" in instrumented
    assert "PTR_DATE_POST_SELECT_WAIT_MS" in instrumented
    assert '_ptr_pick_date_via_icon' in instrumented
    assert '_ptr_click_outside_control' in instrumented
    assert 'xpath=ancestor::oj-input-date[1]' in instrumented
    assert 'input[role="combobox"], input' in instrumented
    assert '.oj-datepicker-popup' in instrumented
    assert 'get_by_role("cell", name=day_label, exact=True)' in instrumented
    assert 'wait_for(state="hidden"' in instrumented
    assert 'current_page.mouse.click' in instrumented
    assert 'current_page.keyboard.press("Tab")' in instrumented
    assert '_ptr_wait_ms("PTR_DATE_POST_SELECT_WAIT_MS", 6000)' in instrumented
    assert 'Date value "' in instrumented
    compile(instrumented, "<instrumented>", "exec")


def test_inject_runtime_helpers_navigation_button_helper_detects_stalled_step() -> None:
    script = """
from playwright.sync_api import sync_playwright


def run(playwright):
    page = None
    browser = playwright.chromium.launch(headless=False)
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
"""

    instrumented = _inject_runtime_helpers(script)

    assert '_ptr_click_navigation_button' in instrumented
    assert '_ptr_select_search_trigger_option' in instrumented
    assert 'PTR_NAV_ADVANCE_TIMEOUT_MS' in instrumented
    assert 'PTR_NAV_STEP_STABLE_MS' in instrumented
    assert 'PTR_NAV_BUSY_EXTRA_TIMEOUT_MS' in instrumented
    assert 'PTR_NAV_TRANSITION_EXTRA_TIMEOUT_MS' in instrumented
    assert 'PTR_NAV_RETRY_TIMEOUT_MS' in instrumented
    assert 'PTR_NAV_RETRY_PAUSE_MS' in instrumented
    assert 'PTR_NAV_FINAL_SETTLE_TIMEOUT_MS' in instrumented
    assert 'Navigation button "' in instrumented
    assert 'did not advance from step' in instrumented
    assert 'started leaving step' in instrumented
    assert '[role="progressbar"]' in instrumented
    assert 'oj-c-progress-circle' in instrumented
    assert 'oj-skeleton' in instrumented
    assert 'get_by_role("link", name=label, exact=True).first' in instrumented
    assert 'current_page.go_back(' in instrumented
    assert '_ptr_has_settled_form_content' in instrumented
    assert 'visibleCount >= 2' in instrumented
    assert '_ptr_get_step_index' in instrumented
    assert '.oj-sp-guided-process-step-number' in instrumented
    assert '[aria-label^="Step "], [title^="Step "]' in instrumented
    assert '_ptr_commit_active_rich_text' in instrumented
    assert 'PTR_NAV_EDITOR_COMMIT_WAIT_MS' in instrumented
    assert "oj-sp-ai-input-rich-text, oj-sp-input-rich-text-2" in instrumented
    assert 'effective_timeout_ms' in instrumented
    assert 'did not advance from step "{step_before}" within {effective_timeout_ms}ms.' in instrumented
    assert 'transition did not stabilize' in instrumented
    assert '.oj-required.oj-searchselect-no-value' in instrumented
    assert '_ptr_click_textbox' in instrumented
    assert 'PTR_SEARCH_POPUP_TIMEOUT_MS' in instrumented
    assert 'Unable to select "' in instrumented
    assert '[class*="loading"]' not in instrumented
    assert '[class*="spinner"]' not in instrumented
    assert '[class*="busy"]' not in instrumented
    compile(instrumented, "<instrumented>", "exec")
