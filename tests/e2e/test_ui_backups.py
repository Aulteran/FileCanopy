"""M06c in a real browser against the standard stack, which has no backup volume mounted.

Covers what an administrator and a member see when backups are not set up: the settings page with
the plain-language warning and a disabled switch for the administrator, only the restore section
for a member (with no administrator controls), and the server refusing the member's direct calls.
The runs, archives, keys and restore with a mounted volume are covered by the API and Compose
suites. Random credentials only.
"""

import pytest
from playwright.sync_api import Browser, expect
from support import World, api_status, sign_in

pytestmark = pytest.mark.e2e


def test_administrator_sees_settings_and_the_missing_volume_warning(
    world: World, browser: Browser
) -> None:
    page = sign_in(browser, world, "admin@example.com")
    page.get_by_role("link", name="Backups").click()
    expect(page.get_by_role("heading", name="Backups", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Backup settings")).to_be_visible()
    warnings = page.get_by_role("list", name="Warnings")
    expect(warnings).to_contain_text("No backup folder is mounted")
    switch = page.get_by_label("Back up automatically")
    expect(switch).to_be_disabled()
    expect(page.get_by_role("button", name="Back up now")).to_be_disabled()
    expect(page.get_by_text("never sent anywhere else")).to_be_visible()
    # nothing about names: the folder table says what kind of folder it is, not whose
    expect(page.get_by_role("cell", name="A personal folder").first).to_be_visible()
    # the page works at a phone width without horizontal scrolling
    page.set_viewport_size({"width": 375, "height": 812})
    scroll_width = page.evaluate("document.documentElement.scrollWidth")
    assert scroll_width <= 375 + 1
    # saving without a volume is refused by the server and the message is plain
    page.set_viewport_size({"width": 1100, "height": 800})
    assert api_status(page, "PUT", "/api/v1/admin/backups/settings", _body()) == 409


def test_member_sees_only_restore_and_cannot_use_the_admin_calls(
    world: World, browser: Browser
) -> None:
    page = sign_in(browser, world, world.alice_mail)
    page.get_by_role("link", name="Backups").click()
    expect(page.get_by_role("heading", name="Restore", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Backup settings")).to_have_count(0)
    expect(page.get_by_text("There are no backups of this folder yet.")).to_be_visible()
    assert api_status(page, "GET", "/api/v1/admin/backups") == 403
    assert api_status(page, "PUT", "/api/v1/admin/backups/settings", _body()) == 403
    assert api_status(page, "POST", "/api/v1/admin/backups/run") == 403
    assert (
        api_status(
            page,
            "POST",
            f"/api/v1/nodes/{world.alice_root}/backups/restore",
            {"recovery_key": "fcbk1-" + "A" * 43},
        )
        == 404
    )  # her own root, but there is no backup to restore


def _body() -> dict[str, object]:
    return {
        "enabled": True,
        "interval_minutes": 60,
        "full_every": 7,
        "min_chains_kept": 2,
        "max_size_bytes": None,
        "encrypt": True,
        "acknowledge_unencrypted": False,
    }
