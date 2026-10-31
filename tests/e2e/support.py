"""Helpers shared by the browser test modules (fixtures live in conftest.py, once, so every
module shares one browser and one stack)."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.sync_api import Browser, Locator, Page, expect
from test_auth_stack import PASSWORD, Client
from test_stack import Stack

expect.set_options(timeout=15_000)


@dataclass
class World:
    stack: Stack
    base: str
    alice_mail: str
    bob_mail: str
    alice: Client
    alice_root: str
    alice_disk: Path


def sign_in(browser: Browser, w: World, email: str, *, setup: bool = False, **ctx: Any) -> Page:
    """Signs in. Someone whose setup window is open first gets the full-page setup screen. With
    `setup=True` that screen is left on the page for the test to use; otherwise "Decide later" is
    pressed for every such screen (the window stays open) so the test lands in the browser."""
    page = browser.new_context(**ctx).new_page()
    page.goto(w.base + "/")
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in").click()
    screen = page.get_by_role("region", name=re.compile("^Set up "))
    nav = page.get_by_role("navigation", name="Locations")
    if setup:
        expect(screen).to_be_visible()
        return page
    later = page.get_by_role("button", name="Decide later")
    for _ in range(4):  # an administrator may have two open roots
        nav.or_(later).first.wait_for()
        if nav.is_visible():
            break
        later.click()
    expect(nav).to_be_visible()
    return page


def api_status(page: Page, method: str, path: str, body: Any = None) -> int:
    """A direct API call made from the signed-in page (cookie, CSRF token and Origin included)."""
    status = page.evaluate(
        """async ([method, path, body]) => {
          const s = await (await fetch('/api/v1/auth/session')).json();
          const r = await fetch(path, {
            method,
            body: body === null ? undefined : JSON.stringify(body),
            headers: {'X-CSRF-Token': s.csrf_token, 'Content-Type': 'application/json'},
          });
          return r.status;
        }""",
        [method, path, body],
    )
    return int(status)


def row(page: Page, name: str) -> Locator:
    return page.get_by_role("row").filter(has_text=name)


def select(page: Page, name: str) -> None:
    row(page, name).locator("td").nth(1).click()  # the size cell: not the folder link


def crumbs(page: Page) -> Locator:
    return page.get_by_role("navigation", name="Breadcrumb")


def upload(page: Page, name: str, data: bytes) -> None:
    page.get_by_test_id("upload-input").set_input_files(
        {"name": name, "mimeType": "application/octet-stream", "buffer": data}
    )


def new_folder(page: Page, name: str) -> None:
    page.get_by_role("button", name="New folder").click()
    dialog = page.get_by_role("dialog", name="New folder")
    dialog.get_by_label("Folder name").fill(name)
    dialog.get_by_role("button", name="Create").click()
    expect(row(page, name)).to_be_visible()


def open_folder(page: Page, name: str) -> None:
    row(page, name).get_by_role("link", name=name).click()
    expect(crumbs(page).get_by_text(name)).to_be_visible()
