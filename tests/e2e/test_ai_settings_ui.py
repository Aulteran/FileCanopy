"""The AI settings dialog in a real browser: dialogs size to their content, the Claude model is
picked from a list with its prices filled in, and prices sit in a collapsed "Advanced" section.

Nothing here saves a provider or calls a paid service.
"""

import re

import pytest
from playwright.sync_api import Browser, Locator, Page, expect
from support import World, sign_in
from test_auth_stack import PASSWORD

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

    # Anthropic is the first cloud provider, so the form starts on it.
    def field(label: str, tag: str) -> Locator:
        return dialog.locator("label").filter(has_text=re.compile(f"^{label}")).locator(tag)

    model = field("Model", "select")
    expect(model).to_have_value("claude-sonnet-5-5")

    # prices are hidden until "Advanced" is opened, and are already filled in
    price_in = field("Price per million input tokens", "input")
    price_out = field("Price per million output tokens", "input")
    expect(price_in).to_be_hidden()
    dialog.get_by_text("Advanced: prices used for the monthly limit").click()
    expect(price_in).to_have_value("2")
    expect(price_out).to_have_value("10")

    # choosing another listed model changes the prices with it
    model.select_option("claude-opus-5-5")
    expect(price_in).to_have_value("4")
    expect(price_out).to_have_value("20")

    # "Other" lets an advanced user type a name and the prices to go with it
    model.select_option(label="Other model (type its name)")
    name = dialog.get_by_label("Model name")
    expect(name).to_be_visible()
    expect(price_in).to_be_visible()
    name.fill("claude-experimental")
    expect(price_in).to_have_value("4")  # not wiped while typing

    # the long cloud notice wraps; its checkbox sits level with the first line, not mid-paragraph
    dialog.get_by_label("Turn this model on").check()
    consent = dialog.locator("label").filter(has_text="Files in Private and Confidential").last
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


def test_first_run_server_step_is_a_centred_column_of_cards(browser: Browser, world: World) -> None:
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
    assert box["width"] <= 740, "a readable column, not the full page width"
    middle = box["x"] + box["width"] / 2
    assert abs(middle - viewport["width"] / 2) < 40, "the column sits in the middle"
    first = screen.get_by_role("listitem").first.bounding_box()
    assert first is not None
    assert first["width"] > box["width"] * 0.9, "each step is a card as wide as the column"
    page.context.close()
