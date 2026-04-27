from playwright.sync_api import Playwright, sync_playwright


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(channel="chromium", headless=False)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()

    page.goto("{{url}}")
    page.get_by_role("textbox", name="Username").click()
    page.get_by_role("textbox", name="Username").fill("{{username}}")
    page.get_by_role("textbox", name="Username").press("Tab")
    page.get_by_role("textbox", name="Password").fill("{{password}}")
    page.get_by_role("textbox", name="Password").press("Enter")

    page.get_by_role("link", name="Home", exact=True).click()
    page.get_by_role("link", name="My Client Groups").click()
    page.get_by_role("link", name="Promote and Change Position", exact=True).click()
    page.get_by_role("cell", name="{{search_by_name_business}}").click()

    page.get_by_role("button", name="Additional position info Add").click()
    page.get_by_role("button", name="Associated profiles Add or").click()
    page.get_by_role("button", name="Compensation Add compensation").click()
    page.get_by_role("button", name="Payroll details Update").click()
    page.get_by_role("button", name="Direct reports Add direct").click()
    page.get_by_role("button", name="Salary Update details such as").click()
    page.get_by_role("button", name="Managers Add or remove").click()
    page.get_by_role("button", name="Legislative info Add or").click()

    page.get_by_role("button", name="Continue").click()
    page.get_by_title("Select Date.").click()
    page.get_by_role("button", name="16").click()

    page.get_by_role("combobox", name="Why do you want to promote?").click()
    page.get_by_role("cell", name="Performance").click()
    page.get_by_role("combobox", name="Position").click()
    page.get_by_role("cell", name="Team Leader Service Desk ES1").click()

    page.get_by_role("button", name="Continue").click()
    page.get_by_role("button", name="Continue").click()
    page.get_by_role("button", name="Continue").click()
    page.get_by_role("button", name="Continue").click()
    page.get_by_role("button", name="Continue").click()
    page.get_by_role("button", name="Continue").click()
    page.get_by_role("button", name="Continue").click()
    page.get_by_role("button", name="Continue").click()

    page.get_by_role("combobox", name="Salary Basis").click()
    page.get_by_role("gridcell", name="ES Annual Salary Basis").click()
    page.get_by_role("textbox", name="Salary Amount EUR Annually").click()
    page.get_by_role("textbox", name="Salary Amount EUR Annually").fill("{{salary_amount_eur_annually}}")
    page.get_by_text("EUR Annually", exact=True).click()

    page.get_by_role("button", name="Continue").click()
    page.get_by_role("button", name="Continue").click()
    page.get_by_role("button", name="Continue").click()
    page.get_by_role("button", name="Continue").click()
    page.get_by_role("button", name="Submit").click()

    page.close()
    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
