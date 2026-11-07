"""The AI settings dialog in a real browser: dialogs size to their content, the Claude model is
picked from a list with its prices filled in, and prices sit in a collapsed "Advanced" section.

Nothing here saves a provider or calls a paid service.
"""

import re

import pytest
from playwright.sync_api import Browser, Locator, Page, expect
from support import World, sign_in
from test_auth_stack import PASSWORD, Client

pytestmark = pytest.mark.e2e


def _open_ai(page: Page) -> None:
    page.get_by_role("button", name=re.compile("Profile menu")).click()
    page.get_by_role("menuitem", name="AI", exact=True).click()
    expect(page.get_by_role("dialog", name="AI")).to_be_visible()


def test_dialogs_fit_their_content_and_scroll_when_tall(browser: Browser, world: World) -> None:
    page = sign_in(browser, world, world.alice_mail)
    viewport = page.viewport_size
    assert viewport is not None

    page.get_by_role("button", name="New folder").click()
    small = page.get_by_role("dialog", name="New folder")
    expect(small).to_be_visible()
    box = small.bounding_box()
    assert box is not None
    assert box["height"] < viewport["height"] * 0.6, "a short dialog must not stretch"
    assert box["width"] >= 560, "dialogs are wide enough to read"
    page.keyboard.press("Escape")
    expect(small).to_be_hidden()

    _open_ai(page)
    tall = page.get_by_role("dialog", name="AI")
    box = tall.bounding_box()
    assert box is not None
    assert box["y"] >= 0 and box["y"] + box["height"] <= viewport["height"], "stays on screen"
    # a long dialog keeps its content reachable by scrolling inside it
    assert tall.evaluate("(d) => d.scrollHeight >= d.clientHeight")
    page.context.close()


def test_claude_model_is_a_list_with_prices_tucked_away(browser: Browser, world: World) -> None:
    page = sign_in(browser, world, "admin@example.com")
    _open_ai(page)
    dialog = page.get_by_role("dialog", name="AI")
    # the administrator turns AI on for the server, then starts adding a model
    # the box follows the server's answer, so click and wait rather than `check()`
    switch = dialog.get_by_label("Enable AI on this server?")
    switch.click()
    expect(switch).to_be_checked()
    dialog.get_by_role("button", name="Add a model").click()
    dialog = page.get_by_role("dialog", name="Add a model")
    # an administrator also has "my own provider" further down: work inside the new model's form
    form = dialog.locator("form").filter(has_text="Name (optional)")

    # Anthropic is the first cloud provider, so the form starts on it.
    def field(label: str, tag: str) -> Locator:
        return form.locator("label").filter(has_text=re.compile(f"^{label}")).locator(tag)

    model = field("Model", "select")
    expect(model).to_have_value("claude-sonnet-5-5")

    # prices are hidden until "Advanced" is opened, and are already filled in
    price_in = field("Price per million input tokens", "input")
    price_out = field("Price per million output tokens", "input")
    expect(price_in).to_be_hidden()
    form.get_by_text("Advanced: prices used for the monthly limit").click()
    expect(price_in).to_have_value("2")
    expect(price_out).to_have_value("10")

    # choosing another listed model changes the prices with it
    model.select_option("claude-opus-5-5")
    expect(price_in).to_have_value("4")
    expect(price_out).to_have_value("20")

    # "Other" lets an advanced user type a name and the prices to go with it
    model.select_option(label="Other model (type its name)")
    name = form.get_by_label("Model name")
    expect(name).to_be_visible()
    expect(price_in).to_be_visible()
    name.fill("claude-experimental")
    expect(price_in).to_have_value("4")  # not wiped while typing

    # the long cloud notice wraps; its checkbox sits level with the first line, not mid-paragraph
    form.get_by_label("Turn this model on").check()
    consent = form.locator("label").filter(has_text="Files in Private and Confidential").last
    expect(consent).to_be_visible()
    gap = consent.evaluate(
        """(l) => {
          const box = l.querySelector('input').getBoundingClientRect();
          const top = l.getBoundingClientRect().top;
          const line = parseFloat(getComputedStyle(l).lineHeight) || 20;
          return [box.top + box.height / 2 - top, line, l.getBoundingClientRect().height];
        }"""
    )
    assert gap[2] > gap[1] * 2, "the notice is expected to wrap onto several lines"
    assert gap[0] < gap[1], "checkbox must align with the first line of the text"
    page.context.close()


def test_add_model_popup_closes_with_x_escape_and_backdrop(browser: Browser, world: World) -> None:
    page = sign_in(browser, world, "admin@example.com")
    _open_ai(page)
    ai = page.get_by_role("dialog", name="AI")
    switch = ai.get_by_label("Enable AI on this server?")
    switch.click()
    expect(switch).to_be_checked()
    add = ai.get_by_role("button", name="Add a model")
    popup = page.get_by_role("dialog", name="Add a model")
    # the X closes it
    add.click()
    expect(popup).to_be_visible()
    popup.get_by_role("button", name="Dismiss dialog").click()
    expect(popup).to_have_count(0)
    # Escape closes only the popup, not the AI dialog under it, and focus returns to the opener
    add.click()
    expect(popup).to_be_visible()
    page.keyboard.press("Escape")
    expect(popup).to_have_count(0)
    expect(ai).to_be_visible()
    expect(add).to_be_focused()
    # a click on the dimmed backdrop closes it
    add.click()
    expect(popup).to_be_visible()
    page.mouse.click(2, 2)
    expect(popup).to_have_count(0)
    page.context.close()


def test_first_run_server_step_is_wide(browser: Browser, world: World) -> None:
    page = browser.new_context().new_page()
    page.goto(world.base + "/")
    page.get_by_label("Email").fill("admin@example.com")
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in").click()
    screen = page.get_by_role("region", name="Prepare your server")
    expect(screen).to_be_visible()
    viewport = page.viewport_size
    assert viewport is not None
    box = screen.bounding_box()
    assert box is not None
    assert box["width"] > viewport["width"] * 0.85, "the layout uses most of the window"
    middle = box["x"] + box["width"] / 2
    assert abs(middle - viewport["width"] / 2) < 40, "the layout sits in the middle"
    first = screen.get_by_role("listitem").first.bounding_box()
    assert first is not None
    assert 0.28 < first["width"] / box["width"] < 0.34, "step cards fill the left third"
    page.context.close()


def test_server_step_marks_only_the_steps_that_are_really_done(
    browser: Browser, world: World
) -> None:
    """A step card shows a "Completed" check mark only for saved state, and keeps it on reload:
    the users step because the world already invited two people. This world has no mail server."""
    page = browser.new_context().new_page()
    page.goto(world.base + "/")
    page.get_by_label("Email").fill("admin@example.com")
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in").click()
    screen = page.get_by_role("region", name="Prepare your server")
    expect(screen).to_be_visible()

    def done(step: str) -> Locator:
        item = screen.get_by_role("listitem").filter(has_text=step)
        return item.get_by_role("img", name="Completed")

    for _ in range(2):  # the second pass is after a reload
        expect(done("Step 3: Outgoing email")).to_have_count(0)
        expect(done("Step 4: Other users")).to_be_visible()
        expect(done("Step 1: AI")).to_have_count(0)
        expect(done("Step 2: Meaning-based search")).to_have_count(0)
        expect(done("Step 5: Incoming mail")).to_have_count(0)
        page.reload()
    page.context.close()


def test_outgoing_email_test_shows_its_result_inside_the_panel(
    browser: Browser, world: World
) -> None:
    """Step 3: a failing test reports why next to the form, without closing it or leaking the
    password, and nothing is saved by testing."""
    page = browser.new_context().new_page()
    page.goto(world.base + "/")
    page.get_by_label("Email").fill("admin@example.com")
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in").click()
    page.get_by_role("button", name="Set up outgoing email").click()
    panel = page.get_by_role("region", name="Step 3: Outgoing email")
    expect(panel).to_be_visible()
    panel.get_by_label("Mail server").fill("127.0.0.1")
    panel.get_by_label("Security", exact=False).select_option("none")
    panel.get_by_label("Port", exact=True).fill("1")  # nothing listens here
    panel.get_by_label("User name").fill("mailer")
    panel.get_by_label("Password", exact=True).fill("never-shown-secret")
    panel.get_by_label("From address").fill("files@example.com")
    panel.get_by_role("button", name="Send a test message").click()
    expect(panel.get_by_role("alert")).to_contain_text("Could not reach the mail server")
    expect(panel).to_be_visible()
    assert "never-shown-secret" not in panel.get_by_role("alert").inner_text()
    page.context.close()


def test_first_time_choosing_a_cloud_server_ai_shows_the_notice_first(
    browser: Browser, world: World
) -> None:
    """Choosing the server's cloud AI shows the notice and waits for the person to agree; nothing
    is saved before that."""
    admin = Client(world.stack)
    assert admin.login("admin@example.com") == 200
    model = {
        "kind": "anthropic",
        "model": "claude-sonnet-5-5",
        "label": "Cloud Test",
        "api_key": "sk-test-not-a-real-key",
        "price_in_micro": 2_000_000,
        "price_out_micro": 10_000_000,
        "enabled": True,
        "acknowledge_cloud": True,
    }
    status, created, _ = admin.call("POST", "/api/v1/ai/instance/models", model)
    assert status == 200, created
    assert admin.call("PUT", "/api/v1/ai/instance/enabled", {"enabled": True})[0] == 200
    try:
        page = sign_in(browser, world, world.alice_mail)
        _open_ai(page)
        dialog = page.get_by_role("dialog", name="AI")
        radio = dialog.get_by_role("radio", name=re.compile("default server AI"))
        expect(dialog.get_by_text("Cloud Test, cloud: Anthropic")).to_be_visible()
        radio.check()
        agree = dialog.get_by_role("button", name="Agree and use the default server AI")
        expect(dialog.get_by_text("never sent to a cloud provider").last).to_be_visible()
        expect(agree).to_be_disabled()
        assert world.alice.call("GET", "/api/v1/ai/status")[1]["preference"] == "off"
        dialog.get_by_label(re.compile("^Cloud AI sends")).check()
        agree.click()
        expect(radio).to_be_checked()
        expect(agree).to_be_hidden()
        assert world.alice.call("GET", "/api/v1/ai/status")[1]["preference"] == "instance_default"
        page.context.close()
    finally:
        world.alice.call("PUT", "/api/v1/ai/me/preference", {"preference": "off"})
        admin.call("DELETE", f"/api/v1/ai/instance/models/{created['id']}")
        admin.call("PUT", "/api/v1/ai/instance/enabled", {"enabled": False})


def test_models_start_as_an_empty_table_and_usage_lives_on_its_own_page(
    browser: Browser, world: World
) -> None:
    page = sign_in(browser, world, "admin@example.com")
    _open_ai(page)
    dialog = page.get_by_role("dialog", name="AI")
    switch = dialog.get_by_label("Enable AI on this server?")
    switch.click()
    expect(switch).to_be_checked()
    # no models yet: a message and the button, no table, and no server-wide limit anywhere
    expect(dialog.get_by_text("No models yet")).to_be_visible()
    expect(dialog.get_by_role("table")).to_have_count(0)
    expect(dialog.get_by_role("button", name="Add a model")).to_be_visible()
    expect(dialog.get_by_text("Monthly limit for cloud models on this server")).to_have_count(0)
    expect(dialog.get_by_text("Estimates, not a bill")).to_have_count(0)
    page.keyboard.press("Escape")

    page.goto(world.base + "/admin/usage")
    expect(page.get_by_role("heading", name="AI usage")).to_be_visible()
    expect(page.get_by_text("Estimates, not a bill")).to_be_visible()
    page.context.close()
