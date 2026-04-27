from playwright.sync_api import Playwright, sync_playwright


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(
        args=["--window-size=1370,840", "--window-position=0,0"],
        channel="chromium",
        headless=False,
    )
    context = browser.new_context(viewport={"width": 1280, "height": 720})
    page = context.new_page()

    page.goto("{{url}}")
    page.get_by_role("textbox", name="Username").click()
    page.get_by_role("textbox", name="Username").fill("{{username}}")
    page.get_by_role("textbox", name="Password").click()
    page.get_by_role("textbox", name="Password").fill("{{password}}")
    page.get_by_role("textbox", name="Password").press("Enter")

    page.get_by_text("{{notifications_label}}").click()
    page.get_by_role("link", name="Show All").click()
    page.get_by_role("combobox", name="Search", exact=True).click()
    page.get_by_role("combobox", name="Search", exact=True).fill("{{search_value}}")
    page.get_by_role("combobox", name="Search", exact=True).press("Enter")
    page.get_by_role("button", name="Approve").click()

    page.close()
    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
