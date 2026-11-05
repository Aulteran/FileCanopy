"""Synthetic no-AI Confidential acceptance, including server refusal and restart."""

import pytest
from playwright.sync_api import Browser, expect
from support import World, api_status, new_folder, open_folder, row, sign_in, upload
from test_auth_stack import PASSWORD

pytestmark = pytest.mark.e2e


def test_confidential_password_lock_and_restart(world: World, browser: Browser) -> None:
    page = sign_in(browser, world, world.alice_mail)
    new_folder(page, "Candidate secret")
    open_folder(page, "Candidate secret")
    folder = page.url.rsplit("/", 1)[-1]
    payload = b"synthetic confidential candidate bytes\n"
    upload(page, "confidential.txt", payload)
    expect(row(page, "confidential.txt")).to_be_visible()
    page.get_by_role("button", name="Privacy…", exact=True).click()
    dialog = page.get_by_role("dialog", name="Privacy", exact=True)
    dialog.get_by_label("Confidential", exact=True).check()
    dialog.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_role("heading", name="Candidate secret is locked")).to_be_visible()
    assert api_status(page, "GET", f"/api/v1/nodes/{folder}/children") == 403
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Unlock", exact=True).click()
    expect(row(page, "confidential.txt")).to_be_visible()
    page.get_by_role("button", name="Lock now", exact=True).click()
    expect(page.get_by_role("heading", name="Candidate secret is locked")).to_be_visible()
    assert api_status(page, "GET", f"/api/v1/nodes/{folder}/children") == 403
    world.stack.compose("restart")
    world.stack.wait_health("ok", timeout=120)
    page.reload()
    expect(page.get_by_role("heading", name="Candidate secret is locked")).to_be_visible()
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Unlock", exact=True).click()
    expect(row(page, "confidential.txt")).to_be_visible()
    assert (world.alice_disk / "Candidate secret" / "confidential.txt").read_bytes() == payload
    page.context.close()
