from src.tools.tools import (
    _default_experience_store_path,
    _derive_parameters_file_candidates,
    _expand_recordings_for_parameter_rows_data,
    _load_runner_env_defaults,
    _merge_runner_env_defaults,
    _parameterise_script,
    _parameters_to_json_object,
    _parse_excel_parameter_sets,
    _parse_excel_parameters,
    _prepare_script_for_execution,
    _split_storage_object_ref,
)


def test_prepare_script_for_execution_rewrites_inline_search_result_clicks() -> None:
    script = """
from playwright.sync_api import Playwright, sync_playwright


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    page = browser.new_page()
    page.get_by_role("textbox", name="Search for people to add as").fill("Fu Jiang")
    page.get_by_text("Fu Jiang").click()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
"""

    prepared = _prepare_script_for_execution(script)

    assert (
        "_ptr_tracked_action('search_and_select', 'Search for people to add as', "
        "_ptr_select_search_trigger_option, page.get_by_role('textbox', "
        "name='Search for people to add as'), page.get_by_text('Fu Jiang'), "
        "page, 'Search for people to add as', 'Fu Jiang', option_kind='text', fill_value='Fu Jiang')"
        in prepared
    )
    assert "_ptr_set_script_data({'tracked_action': 'search_and_select'" in prepared
    assert "_ptr_tracked_action('fill_textbox', 'Search for people to add as'" not in prepared
    assert "_ptr_tracked_action('click_text', 'Fu Jiang'" not in prepared


def test_prepare_script_for_execution_drops_premature_continue_before_reporting_relationship() -> None:
    script = """
from playwright.sync_api import Playwright, sync_playwright


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=False)
    page = browser.new_page()
    page.get_by_role("combobox", name="Search for people to add as").click()
    page.get_by_role("textbox", name="Search for people to add as").fill("Fu")
    page.get_by_text("Wan Fu").click()
    page.get_by_role("button", name="Continue").click()
    page.get_by_role("combobox", name="Reporting Relationship").click()
    page.get_by_role("option", name="Project Manager").click()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
"""

    prepared = _prepare_script_for_execution(script)

    assert (
        "_ptr_tracked_action('search_and_select', 'Search for people to add as', "
        "_ptr_select_search_trigger_option, page.get_by_role('textbox', "
        "name='Search for people to add as'), page.get_by_text('Wan Fu'), "
        "page, 'Search for people to add as', 'Wan Fu', option_kind='text', fill_value='Fu')"
        in prepared
    )
    assert (
        "_ptr_tracked_action('select_combobox', 'Reporting Relationship', "
        "_ptr_select_combobox_option, page.get_by_role('combobox', "
        "name='Reporting Relationship'), page.get_by_role('option', "
        "name='Project Manager'), page, 'Reporting Relationship', "
        "'Project Manager')"
        in prepared
    )
    assert "_ptr_tracked_action('click_combobox', 'Search for people to add as'" not in prepared
    assert "_ptr_tracked_action('navigation_button', 'Continue'" not in prepared


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


def test_load_runner_env_defaults_parses_exported_values(tmp_path) -> None:
    config_path = tmp_path / "configs.txt"
    config_path.write_text(
        "\n".join(
            [
                "# comment",
                "export PTR_CAPTURE_STEPS=true",
                "PTR_POST_CLICK_WAIT_MS=250",
                'export PTR_GREETING="hello world"',
                "invalid line",
            ]
        ),
        encoding="utf-8",
    )

    defaults = _load_runner_env_defaults(config_path)

    assert defaults == {
        "PTR_CAPTURE_STEPS": "true",
        "PTR_POST_CLICK_WAIT_MS": "250",
        "PTR_GREETING": "hello world",
    }


def test_merge_runner_env_defaults_keeps_explicit_env_values(tmp_path) -> None:
    config_path = tmp_path / "configs.txt"
    config_path.write_text(
        "\n".join(
            [
                "export PTR_RECORD_VIDEO=false",
                "export PTR_POST_CLICK_WAIT_MS=250",
            ]
        ),
        encoding="utf-8",
    )

    merged = _merge_runner_env_defaults(
        {
            "PTR_POST_CLICK_WAIT_MS": "900",
            "CUSTOM_VALUE": "present",
        },
        config_path=config_path,
    )

    assert merged["PTR_RECORD_VIDEO"] == "false"
    assert merged["PTR_POST_CLICK_WAIT_MS"] == "900"
    assert merged["CUSTOM_VALUE"] == "present"


def test_default_experience_store_path_uses_runner_data_dir(tmp_path) -> None:
    path = _default_experience_store_path(tmp_path)

    assert path == tmp_path.resolve() / ".runner_data" / "experience.jsonl"


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


def test_parse_excel_parameter_sets_prefers_parameter_value_sheet_over_horizontal_rows() -> None:
    from io import BytesIO

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Parameter", "Value", "Description", "delay_before_ms"])
    ws.append(["startUrl", "https://iamoqy-test.fa.ocs.oraclecloud.com/", "Base URL", ""])
    ws.append(["Username", "svc", "Login user", ""])
    ws.append(["Password", "Calfus@123", "Login password", ""])
    ws.append(["Search by Name Business", "Apple, Inc.", "Worker search", "500"])

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
                "search_by_name_business": "Apple, Inc.",
            },
        }
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
