"""M08 in a real browser: the owner shares a folder, the other person is told and can only read,
and removing the share closes it at once. Random credentials only."""

import re

import pytest
from playwright.sync_api import Browser, expect
from support import World, api_status, new_folder, sign_in

pytestmark = pytest.mark.e2e


def test_share_read_only_then_revoke(world: World, browser: Browser) -> None:
    # Bob gets a name of his own so the picker finds exactly him (every test account is "Alice").
    nick = "Bobby" + world.bob_mail.split("@")[0][3:]
    world.stack.sql(f"update app_user set name = '{nick}' where email = '{world.bob_mail}'")
    alice = sign_in(browser, world, world.alice_mail)
    new_folder(alice, "Holiday plans")
    alice.get_by_role("link", name="Holiday plans").click()
    alice.get_by_role("button", name=re.compile("^Share")).click()
    dialog = alice.get_by_role("dialog", name=re.compile("Share"))
    dialog.get_by_label("Add people").fill(nick)
    dialog.get_by_role("button", name=nick).click()
    dialog.get_by_role("button", name="Add").click()
    expect(dialog.get_by_role("list", name="People with access")).to_contain_text(nick)
    expect(dialog.get_by_text("Everything inside is shared too")).to_be_visible()
    dialog.get_by_role("button", name="Done").click()
    expect(alice.get_by_role("button", name="Share (1)")).to_be_visible()

    # A subfolder shows the access it inherits from the share above it.
    new_folder(alice, "Sub")
    alice.get_by_role("link", name="Sub").click()
    alice.get_by_role("button", name=re.compile("^Share")).click()
    inherited = alice.get_by_role("dialog", name=re.compile("Share"))
    expect(inherited.get_by_text("through")).to_be_visible()
    expect(inherited.get_by_role("link", name="Holiday plans")).to_be_visible()
    expect(inherited.get_by_text("Not shared with anyone yet")).to_have_count(0)
    inherited.get_by_role("button", name="Done").click()
    alice.go_back()

    bob = sign_in(browser, world, world.bob_mail)
    expect(bob.get_by_role("link", name=re.compile(r"Notifications \(1 new\)"))).to_be_visible()
    bob.get_by_role("link", name=re.compile("Notifications")).click()
    expect(bob.get_by_text("shared the folder Holiday plans with you")).to_be_visible()
    bob.get_by_role("link", name=re.compile("Holiday plans")).first.click()
    expect(bob.get_by_text("Read-only")).to_be_visible()
    expect(bob.get_by_role("button", name="New folder")).to_be_disabled()
    expect(bob.get_by_role("button", name=re.compile("^Share"))).to_have_count(0)
    url = bob.url
    node = url.rsplit("/", 1)[1]
    assert api_status(bob, "POST", f"/api/v1/nodes/{node}/folders", {"name": "x"}) == 403
    assert api_status(bob, "GET", f"/api/v1/nodes/{node}/grants") == 403

    # Removing the share closes it for the open session on the very next request.
    alice.get_by_role("button", name="Share (1)").click()
    alice.get_by_role("button", name=re.compile("^Remove")).click()
    alice.get_by_role("button", name="Done").click()
    assert api_status(bob, "GET", f"/api/v1/nodes/{node}") == 403
