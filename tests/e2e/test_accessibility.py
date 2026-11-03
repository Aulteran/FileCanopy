"""M23: accessibility smoke and keyboard flows, in both colour schemes and on a phone.

No third-party audit engine is loaded (the page makes no off-origin request and neither does the
test): a small in-page audit checks the things a screen reader and a keyboard user depend on.
Every reachable screen must have a language, a title, one main landmark, no duplicate ids, a name
on every control and image, and no focusable element hidden from assistive technology. The
keyboard flows then walk the real controls: the skip link, a tab walk with a visible focus
indicator on every stop, and a dialog that takes focus, keeps it, closes on Escape and gives focus
back. Contrast and the rest of WCAG 2.2 AA were checked with axe during M21b.
"""

import pytest
from playwright.sync_api import Browser, Page, expect
from support import World, sign_in

pytestmark = pytest.mark.e2e

AUDIT = """
() => {
  const bad = [];
  const text = (el) => (el.textContent || '').trim();
  const nameOf = (el) => {
    const label = el.getAttribute('aria-label');
    if (label && label.trim()) return label.trim();
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const t = by.split(/\\s+/).map((id) => document.getElementById(id)).filter(Boolean)
        .map(text).join(' ').trim();
      if (t) return t;
    }
    if (el.labels && el.labels.length) {
      const t = Array.from(el.labels).map(text).join(' ').trim();
      if (t) return t;
    }
    if (el.closest('label') && text(el.closest('label'))) return text(el.closest('label'));
    if (text(el)) return text(el);
    const img = el.querySelector('img[alt], svg[aria-label], [aria-label]');
    if (img) return (img.getAttribute('alt') || img.getAttribute('aria-label') || '').trim();
    return (el.getAttribute('title') || '').trim();
  };
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  if (!document.documentElement.lang) bad.push('html has no lang');
  if (!document.title.trim()) bad.push('document has no title');
  const mains = document.querySelectorAll('main, [role=main]');
  if (mains.length !== 1) bad.push('expected one main landmark, found ' + mains.length);
  const seen = new Set();
  document.querySelectorAll('[id]').forEach((el) => {
    if (seen.has(el.id)) bad.push('duplicate id ' + el.id);
    seen.add(el.id);
  });
  const controls = 'button, a[href], select, textarea, ' +
    'input:not([type=hidden]):not([type=file]), ' +
    '[role=button], [role=tab], [role=menuitem], [role=checkbox], [role=switch]';
  document.querySelectorAll(controls).forEach((el) => {
    if (!visible(el)) return;
    if (!nameOf(el)) bad.push('no accessible name: ' + el.outerHTML.slice(0, 120));
  });
  document.querySelectorAll('img').forEach((el) => {
    if (!el.hasAttribute('alt')) bad.push('img without alt: ' + el.outerHTML.slice(0, 120));
  });
  document.querySelectorAll('dialog[open], [role=dialog], [role=alertdialog]').forEach((el) => {
    if (!nameOf(el)) bad.push('dialog without a name');
  });
  document.querySelectorAll('[aria-hidden=true]').forEach((el) => {
    const f = el.querySelectorAll(
      'a[href], button:not([disabled]), input:not([disabled]), [tabindex]');
    f.forEach((x) => {
      if (x.getAttribute('tabindex') !== '-1' && visible(x)) {
        bad.push('focusable inside aria-hidden: ' + x.outerHTML.slice(0, 100));
      }
    });
  });
  document.querySelectorAll('[tabindex]').forEach((el) => {
    if (parseInt(el.getAttribute('tabindex'), 10) > 0) bad.push('positive tabindex');
  });
  return bad;
}
"""

FOCUS_STYLE = """
() => {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const s = getComputedStyle(el);
  const outline = s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0;
  const shadow = !!s.boxShadow && s.boxShadow !== 'none';
  return { outline, shadow, tag: el.tagName, html: el.outerHTML.slice(0, 100) };
}
"""


def screens(page: Page) -> list[str]:
    """The addresses of every sidebar entry, so each screen is audited as the user reaches it."""
    hrefs = page.evaluate(
        """() => Array.from(document.querySelectorAll('nav a[href]'))
             .map((a) => a.getAttribute('href'))
             .filter((h) => h && h.startsWith('/') && !h.startsWith('//'))"""
    )
    return sorted(set(hrefs))


def audit(page: Page, where: str) -> None:
    problems = page.evaluate(AUDIT)
    assert problems == [], f"{where}: {problems}"


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_every_screen_passes_the_structural_audit_in_both_schemes(
    browser: Browser, world: World, scheme: str
) -> None:
    page = sign_in(browser, world, world.alice_mail, color_scheme=scheme)
    audit(page, f"browser ({scheme})")
    for href in screens(page):
        page.goto(world.base + href)
        page.locator("main").first.wait_for()
        audit(page, f"{href} ({scheme})")
    page.context.close()


def test_the_phone_layout_passes_the_audit_and_has_no_sideways_scroll(
    browser: Browser, world: World
) -> None:
    page = sign_in(
        browser,
        world,
        world.alice_mail,
        viewport={"width": 375, "height": 812},
        is_mobile=True,
        has_touch=True,
    )
    audit(page, "phone browser")
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 1, f"page scrolls sideways by {overflow}px"
    page.context.close()


def test_skip_link_is_first_and_moves_focus_to_the_content(browser: Browser, world: World) -> None:
    page = sign_in(browser, world, world.alice_mail)
    page.goto(world.base + "/")
    page.locator("main").first.wait_for()
    page.keyboard.press("Tab")
    skip = page.locator("a.skip")
    expect(skip).to_be_focused()
    expect(skip).to_be_visible()  # off screen until focused, then on screen
    page.keyboard.press("Enter")
    expect(page.locator("#main")).to_be_focused()
    page.context.close()


def test_a_tab_walk_shows_a_focus_indicator_on_every_stop(browser: Browser, world: World) -> None:
    page = sign_in(browser, world, world.alice_mail)
    page.goto(world.base + "/")
    page.locator("main").first.wait_for()
    stops = 0
    unseen: list[str] = []
    for _ in range(40):
        page.keyboard.press("Tab")
        style = page.evaluate(FOCUS_STYLE)
        if style is None:
            continue
        stops += 1
        if not (style["outline"] or style["shadow"]):
            unseen.append(style["html"])
    assert stops >= 8, "the tab walk reached too few controls"
    assert unseen == [], f"no visible focus indicator on: {unseen}"
    page.context.close()


def test_a_dialog_takes_keeps_and_returns_keyboard_focus(browser: Browser, world: World) -> None:
    page = sign_in(browser, world, world.alice_mail)
    opener = page.get_by_role("button", name="New folder")
    opener.focus()
    page.keyboard.press("Enter")
    dialog = page.get_by_role("dialog", name="New folder")
    expect(dialog).to_be_visible()
    inside = "(d) => d.contains(document.activeElement)"
    assert dialog.evaluate(inside), "focus did not move into the dialog"
    audit(page, "new folder dialog")
    for _ in range(12):  # a walk longer than the dialog has controls never leaves it
        page.keyboard.press("Tab")
        assert dialog.evaluate(inside), "focus escaped the open dialog"
    for _ in range(12):
        page.keyboard.press("Shift+Tab")
        assert dialog.evaluate(inside), "focus escaped the open dialog backwards"
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(opener).to_be_focused()  # focus goes back to what opened it
    page.context.close()


def test_reduced_motion_leaves_nothing_animating(browser: Browser, world: World) -> None:
    page = sign_in(browser, world, world.alice_mail, reduced_motion="reduce")
    page.goto(world.base + "/")
    page.locator("main").first.wait_for()
    running = page.evaluate(
        """() => document.getAnimations()
             .filter((a) => a.playState === 'running' && a.effect &&
               (a.effect.getComputedTiming().iterations === Infinity ||
                a.effect.getComputedTiming().duration > 400))
             .map((a) => a.id || (a.effect.target && a.effect.target.tagName))"""
    )
    assert running == [], f"long or endless animations with reduced motion: {running}"
    page.context.close()
