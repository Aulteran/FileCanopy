# Web UI audit (2026-11-08)

A review of the whole web app (`apps/web`) for consistency, polish and usability. Nothing in the code
was changed by this audit. Each finding is sized so that one small fix thread can implement it.

## How this was checked

- Read every page component in `apps/web/src` and all of `styles.css`.
- Ran the current `dev` web app (Vite dev server) against a local API and PostgreSQL, with a seeded
  library (nested folders, long names, a symbolic link, duplicate text files, images, PDFs, three
  extra accounts). Looked at every page, menu and the main dialogs at 1440 px and 390 px wide, in light
  and dark mode (about 100 screenshots).
- First-time setup was looked at only briefly, as asked. Recent work is not re-reported: setup right
  panel scroll, invite form, Step 5 import log, My files setup prompt and the nav hazard triangle.

## Severity

- **High**: visibly broken or confusing on a main page, or affects many pages at once.
- **Medium**: clearly unpolished or inconsistent; users will notice.
- **Low**: small polish, copy or code hygiene.

## Suggested order

1. The **G** (global) items first, especially G01, G02, G03, G05, G18: each is a few lines of CSS or
   markup and fixes many pages at once. Several page items assume them.
2. Then High items: B01, B02, B04, D01, U01, R01, N02.
3. Then the rest by page. Items that touch the same file should not run in parallel threads
   (most Browser items touch `Browser.tsx`; most global items touch `styles.css`).

Every fix should keep `npm run typecheck` and `npm test` (in `apps/web`) green, and keep the existing
e2e selectors (button names, labels) unless the item says otherwise; when a visible label changes,
update the matching `tests/e2e` selectors in the same change.

---

## G. Global styles and shared patterns

### G01. `.muted` class is used about 75 times but never defined (High)
- **Where:** `styles.css`; used in AiSettings, Collections, ConnectedApps, Duplicates, Emails,
  IncomingMailSetup, MailPages, Organize, PreviewSettings, Privacy, ServerSettings, SmtpSettings,
  UsagePage, Users, Account, LockScreen.
- **Problem:** Secondary text (explanations, sizes, locations, roles) renders at full text color, so
  pages like Collections, Duplicates, Usage and the AI dialog look like walls of equal-weight text.
- **Fix:** Add `.muted { color: var(--muted); }` to `styles.css` (next to `.hint`). Nothing else.

### G02. Typed text, select values and placeholders are bold (High)
- **Where:** global; every input wrapped in a `<label>` (labels are `font-weight: 700` and inputs use
  `font: inherit`). Seen in Ask, Share ("View"), Server settings ("30"), Mailbox placeholders
  ("smtp.example.com"), Backups field hints ("The runs in between only copy what changed.").
- **Problem:** Values and placeholders look like labels; placeholders look like filled-in values.
- **Fix:** In `styles.css` add `input, select, textarea { font-weight: 400; }` and
  `::placeholder { color: var(--muted); opacity: 1; font-weight: 400; }`, and
  `label .hint, label small, label .field-hint { font-weight: 400; }`. Check Backups hints by eye.

### G03. Many error messages are plain black text (Medium)
- **Where:** `<p role="alert">` without `className="error"` in Ask.tsx:72, Collections.tsx (128, 220,
  242, 603, 612), ConnectedApps.tsx:73, Duplicates.tsx:141, Emails.tsx:70, NotificationsBell.tsx:99,
  Organize.tsx:176, Privacy.tsx (107, 112, 258), Search.tsx:71, ServerSettings.tsx (94, 111),
  Trash.tsx:111.
- **Problem:** Errors look like ordinary sentences and are easy to miss.
- **Fix:** Add `className="error"` to each of those elements. No CSS change.

### G04. Success messages have no shared style (Low)
- **Where:** `.notice` (Trash, Recent), plain `<p role="status">` (Duplicates, Collections),
  `.smtp-ok` (mail), screen-reader-only (Browser, see B04).
- **Problem:** Each page confirms an action differently, some invisibly.
- **Fix:** Define one `.notice` style in `styles.css` (green check icon space, `color: var(--primary)`,
  `margin: 6px 0`) and use `className="notice"` on every `role="status"` success paragraph in
  Duplicates, Collections, Organize, ServerSettings, SmtpSettings (replace `.smtp-ok`).

### G05. Empty states are inconsistent (Medium)
- **Where:** dashed `.empty` box: My files, Trash, Recent actions. Plain `<p>`: Shared with you,
  Collections, Duplicates ("No duplicates found"), AI suggestions ("No suggestions are waiting"),
  Usage ("No models yet"), Mailbox held messages ("Nothing is held"), Backups restore ("There are no
  backups of this folder yet"), Bell. Non-admins on admin routes see a bare `<p>` (App.tsx 783-847).
- **Problem:** Half the app shows a designed empty state, the other half a stray sentence.
- **Fix:** Use `<p className="empty">` (existing style) for each listed empty message, keeping the
  text. For the non-admin messages in App.tsx also use `.empty`.

### G06. Loading states are bare text in different words and places (Medium)
- **Where:** App.tsx:159 and :380 (whole-page "Loading…" at the top left with no shell), Browser,
  Trash, Recent, Collections ("Loading…"), Duplicates ("Looking…"), Search ("Searching…"), Bell,
  Viewer ("Opening…").
- **Problem:** The first screen after sign-in is an unstyled word in the corner; elsewhere loading looks
  like body text.
- **Fix:** Add a tiny `Loading` component (in `Icon.tsx` or a new `Loading.tsx`): centered muted text
  with a small CSS spinner, `role="status"`. Use it in all listed places; for the two App.tsx
  whole-page cases center it on the page with the Wordmark above.

### G07. Native `window.confirm()` boxes next to styled dialogs (Medium)
- **Where:** AiSettings.tsx (902, 959, 1024), BackupJobs.tsx:158, Backups.tsx:146, Browser.tsx:462
  (stop using a share), Collections.tsx (312, 329), Duplicates.tsx:56, MailPages.tsx (268, 291).
  Trash already uses a styled Modal (`ConfirmForever`).
- **Problem:** Browser-grey system popups break the look and cannot show danger styling.
- **Fix:** Add `ConfirmDialog` to `Dialogs.tsx` (props: title, body, confirmLabel, danger, onConfirm,
  onClose; built on `Modal`, footer Cancel + confirm button, `danger primary` when danger). Replace the
  calls. Can be split: **G07a** add the component and convert Browser, Duplicates, Collections;
  **G07b** convert AiSettings, Backups, BackupJobs, MailPages.

### G08. Form fields stretch across the whole page (Medium)
- **Where:** Server settings (a 1160 px wide number field for "30" days, full-width select),
  Collections "New collection" field, Account and AI dialogs are fine (dialog width).
- **Problem:** Short values in giant fields look unfinished and are hard to scan.
- **Fix:** Add `.stack-form { max-width: 460px; }` as a global rule (it exists only under `.backups`
  and `.users`) and wrap the Server settings sections' fields in it; give number inputs
  `max-width: 10rem`. Collections is handled in C02.

### G09. Selects do not match inputs (Low)
- **Where:** `styles.css` second `select { ... }` block (around line 899) overrides the shared input
  rule: radius 6 px instead of 10 px, min-height 40 px instead of 44 px.
- **Problem:** A select next to an input is shorter and squarer (Ask form, Mail server "Security" +
  "Port" row).
- **Fix:** Delete that second `select` block so selects use the shared `input, select, textarea` rule.

### G10. Primary button use is inconsistent (Low)
- **Where:** Backups "Save" is a plain button while Server settings and Mailbox "Save" are primary;
  Search "Ask" is plain; Share dialog "Add" is primary while the dialog's commit is "Done".
- **Fix:** One primary per form, on the commit action: make Backups "Save" and Ask "Ask" primary; make
  Share "Add" a plain button.

### G11. Bell badge has poor contrast in dark mode, and other hard-coded colors (Medium)
- **Where:** `styles.css` `.bell-badge` (`color: #fff` on `--danger`, which is light pink `#ff8a80` in
  dark mode, about 2:1 contrast), `.step-required` (`#c0392b`), `.sidenav a .navwarn` (`#d9b44a`),
  panel shadows `rgb(0 0 0 / 0.15)` in `.profile-panel` and `.bell-panel`, fallbacks like
  `var(--danger, #b3261e)`.
- **Fix:** Badge text `color: var(--on-primary)` in light and `#1c0a08` in dark (add a
  `--on-danger` token to both themes and use it). `.step-required` → `var(--danger)`, `.navwarn` →
  `var(--sun)`, panel shadows → `var(--shadow)`, drop the `var(--x, #hex)` fallbacks.

### G12. Duplicate and conflicting CSS rules (Low)
- **Where:** `styles.css`: `.sr-only` twice (different clip methods), `.fi.folder` twice, `.searchbox`
  twice, `progress` twice, `dialog` twice, `.topbar .toplink` twice, `select` twice (see G09).
- **Fix:** Merge each pair into one rule, keeping the later values where they differ. Pure cleanup; no
  visual change expected (check My files, top bar and a dialog by eye).

### G13. Wording drift (Low)
- **Where and fix:**
  - "trash" lower-case → "Trash" (the place) in Duplicates.tsx (57, 65, 112, 127), Organize.tsx
    (152, 312), ServerSettings.tsx (126, 138).
  - "shared tree" → "Server files" in ServerSettings.tsx (14, 15, 159: "Server files for new
    accounts").
  - Trash.tsx `rootName`: "the server files" / "your folder" → "Server files" / "My files".
  - Account.tsx:247 "(Lock in the top bar)" → "(Lock in your profile menu)".
  - Admin card "Mailbox", page "Mailbox and quarantine", setup "Mail server", section "Held
    messages": rename the admin card and page title to "Mail server" and keep "Held messages".

### G14. Copy still says AI is optional (Medium)
- **Where:** AdminPage.tsx:231 (setup Step 1 "FileCanopy works without it."), AdminPage.tsx:90
  (Admin card "Optional AI providers…"), AiSettings.tsx:750 ("Optional. When an administrator…"),
  AiSettings.tsx:1049 ("FileCanopy works without any.").
- **Problem:** Contradicts the AI-first positioning (owner decision 2026-11-06).
- **Fix:** Lead with what AI does. Suggested: Step 1 "Choose how AI organizes, searches and answers
  questions about your files."; card "AI models, what they may see, and spending limits."; drop the
  "Optional." opener and "FileCanopy works without any." sentences.

### G15. Mixed quote styles (Low)
- **Where:** Share.tsx dialog title `Share "My files"`, BackupJobs.tsx:158 `"${job.name}"`; elsewhere
  curly quotes (`Move “notes.md”`).
- **Fix:** Use curly quotes “…” in those strings.

### G16. Close buttons are a text "×" with mixed names (Low)
- **Where:** Dialogs.tsx `Modal` close (aria-label "Dismiss dialog", title "Close"), Browser toast
  dismiss and upload rows.
- **Fix:** Use `<Icon name="close" />` (exists in iconPaths) in all three; aria-label "Close" for the
  dialog, keep the specific "Dismiss …" labels on upload rows.

### G17. Blank space reserved by empty message lines (Low)
- **Where:** `.error { min-height: 1.4em }` and empty `<p role="status" class="notice">` on Browser,
  Trash, Recent actions, dialogs: a visible gap under the toolbar or heading when nothing is shown.
- **Fix:** `.error:empty, .notice:empty { display: none; }` in `styles.css`.

### G18. `.plain` list class is undefined, so lists show bullets (Medium)
- **Where:** used in Activity, BackupJobs, Collections, Duplicates, MailPages, Organize.
- **Fix:** Add `.plain { list-style: none; margin: 0; padding: 0; }` and
  `.plain > li { padding: 8px 0; border-bottom: 1px solid var(--line); }`.
  Also define `.card { background: var(--surface); border: 1px solid var(--line); border-radius:
  var(--r-l); padding: 16px 18px; }` and `.panel { margin-bottom: 20px; }` (both used but undefined,
  in AdminPage, Privacy, Account, AiSettings, Users, NotificationsBell); check those pages by eye.

---

## N. Shell: top bar, side navigation, menus

### N01. Side navigation grouping hides AI in a junk drawer (Medium)
- **Where:** App.tsx 594-767.
- **Problem:** "History and tools" holds Trash, Recent actions, Email inbox, Collections, Duplicates
  and AI suggestions; its `aria-label` says "Trash and history". AI, the core of the product, is the
  last item of a miscellaneous group.
- **Fix:** Four groups: **Files** (My files, Server files, Email inbox when on), **Organize**
  (AI suggestions, Collections, Duplicates), **Shared files** (Shared with you, Shared by you),
  **History** (Recent actions, Trash). Visible headings and `aria-label`s match.

### N02. No way to reach "Ask about your files" except by searching first (High)
- **Where:** Ask.tsx is rendered only at the bottom of Search.tsx.
- **Problem:** Chatting with your files is a headline feature but has no entry point; it sits below all
  results and is prefilled with the search keyword (not a question).
- **Fix:** Add an `ASK_PATH = "/ask"` route in `route.ts`, a nav item "Ask AI" (sparkles icon) at the
  top of the Organize group, and a page that renders `<h1>Ask AI</h1>` + `AskPanel` with an empty
  question and placeholder "Ask a question about your files". On the Search page keep the panel but
  start it empty (placeholder `Ask about “{q}”`). Add a `route.test.ts` case.

### N03. Search field and Search button look like two separate controls (Low)
- **Where:** App.tsx 397-419, `.searchbox` in styles.css.
- **Fix:** Put the search icon inside the field on the left (padding-left 36px, absolutely positioned
  icon), hide the button visually (`className="sr-only"`, keep it as the submit for screen readers).

### N04. Account uses the same icon as Admin (Low)
- **Where:** App.tsx:462 (Account menu item uses `settings`, as does the Admin top link).
- **Fix:** Add a simple "user" icon path to `iconPaths.tsx` (head circle + shoulders, Lucide style) and
  use it for Account.

### N05. Profile menu: Sign out alignment and keyboard support (Medium)
- **Where:** ProfileMenu.tsx; styles.css `.profile-panel > button.danger` (justify-content: center).
- **Problem:** Items are right-aligned but Sign out is centered. The panel has no `role="menu"`, focus
  stays on the avatar when it opens, arrow keys do nothing.
- **Fix:** Remove the `justify-content: center` from `.profile-panel > button.danger`. Add
  `role="menu"` to the panel, focus the first `[role=menuitem]` on open, ArrowDown/ArrowUp move focus,
  Home/End jump. Apply the same to the bell panel's Open links only if trivial.

### N06. Notification panel has no header and cramped rows (Low)
- **Where:** NotificationsBell.tsx, `.notices` in styles.css.
- **Fix:** Add a header row "Notifications" (h2, 1rem). Render each item on two lines: text, then muted
  `date · Open`. Empty text: "No notifications yet." (it is not only about shares).

### N07. Admin sub-pages have no way back and Admin is not highlighted on Backups (Low)
- **Where:** Users, Mailbox, Server settings, Usage, Backups (when reached from Admin); App.tsx:435.
- **Fix:** Small `AdminCrumb` component rendering `Administration › <Page>` (link back to `/admin`)
  above the h1 on Users, Mailbox, Server settings and Usage, and on Backups when the user is an admin.
  Include `backups` in the Admin link's `aria-current` condition for admins.

### N08. Boot and fallback screens are unstyled (Low)
- **Where:** App.tsx:148-158 ("Cannot reach the server"), App.tsx:770 ("No storage locations are
  available to you."), skip link text "Skip to files" on every page.
- **Fix:** Render the "Cannot reach the server" state inside the `.auth` card with the Wordmark; put
  the no-locations message in `.empty`; rename the skip link to "Skip to content".

---

## B. File browser (My files, Server files, any folder)

### B01. Toolbar has up to 12 equal buttons and wraps (High)
- **Where:** Browser.tsx 732-850.
- **Problem:** Upload here, Upload folder, Unpack zip here, New folder, Suggest collections, Privacy…,
  Share, Stop using this share, Rescan, Compact list (plus badge and "Read-only") all look the same;
  at 1440 px it wraps to two rows and on a phone to four, pushing the list below the fold. Three
  upload buttons share one icon.
- **Fix:** Split in two threads:
  - **B01a Upload menu:** replace the three upload buttons with one primary "Upload" menu button
    (items: Files…, Folder…, Unpack a .zip…). Build a small `MenuButton` component (pattern from
    ProfileMenu: Escape and outside click close). Keep the hidden inputs and their `data-testid`s.
  - **B01b Overflow menu:** move Rescan, Compact list and Stop using this share into a "More" (`more`
    icon) `MenuButton` at the end of the toolbar. Order the rest: Upload, New folder, Share, Privacy,
    Suggest collections (sparkles icon).

### B02. Modified dates wrap onto two lines in every row (High)
- **Where:** Browser.tsx list `td.when`, format.ts `formatDate`.
- **Problem:** "11/8/2026, 8:49:31 AM" breaks into two lines, doubling row height (compact list too).
- **Fix:** `.list td.when, .list th.when { white-space: nowrap; }`. Add `formatShortDate(iso)` to
  format.ts: today → time ("8:49 AM"), this year → "Nov 8", older → "Nov 8, 2025" (use
  `Intl.DateTimeFormat`), with tests in format.test.ts. Use it in the list; put the full
  `formatDate` in the cell's `title`.

### B03. The folder page has no title (Medium)
- **Where:** Browser.tsx breadcrumb (the current folder is a small bold `<span>`).
- **Problem:** Every other page has a 1.6rem h1; a folder page starts with a 16 px crumb.
- **Fix:** Render the breadcrumb only for ancestors (hide it when at the top) and show the current
  folder name as `<h1>` below it, followed by the privacy badge.

### B04. Most action results are invisible (High)
- **Where:** Browser.tsx `notice` (rendered only in a `.sr-only` status) and the toast (shown only when
  `undoable`).
- **Problem:** "Uploaded x", "Rescan requested…", "Your choice was saved", "Added to collection",
  partial move results show nothing on screen.
- **Fix:** Show the toast whenever `notice` is non-empty; show the Undo button only when `undoable`.
  Auto-hide after 6 s unless hovered or focused; keep the dismiss button.

### B05. Every name is an underlined green link (Medium)
- **Where:** `.list td a` (global `a` color, default underline).
- **Fix:** `.list td a { color: var(--text); text-decoration: none; }` and underline on
  `:hover`/`:focus-visible`.

### B06. Details panel is a tall stack of identical buttons (Medium)
- **Where:** Browser.tsx 1162-1253.
- **Problem:** Up to ten full-width buttons without icons or grouping; "Delete" looks harmless and is
  named differently from the bulk "Move to Trash".
- **Fix:** Open (primary) and Download side by side; then Rename, Move…, Copy…, Add to collection…
  with icons (`rename`, `move`, `copy`, `layers`); then an "AI" group (Suggest organization, Suggest
  PDF split) with the sparkles icon; a divider; "Move to Trash" with `className="danger"` and the
  `trash` icon. Update e2e selectors that use the name "Delete".

### B07. Columns jump between folders (Medium)
- **Where:** the list table uses automatic layout.
- **Fix:** `.list table { table-layout: fixed; }` with widths: pick column 36 px, Size 96 px,
  Modified 140 px (after B02), Name takes the rest.

### B08. Row status flags are long red sentences glued to the name (Low)
- **Where:** Browser.tsx 1110-1131.
- **Problem:** "link-to-elsewhere(not supported: symbolic link)" with no space, italic red; the
  case-collision flag is a full sentence in the name column.
- **Fix:** Render each flag as a small `.badge` (muted color for "not supported", `--warning` for
  "missing"/"needs rescan"/"similar name") with short text ("Not supported", "Missing", "Needs
  rescan", "Similar name") and the full explanation in `title`. The details panel keeps the sentence.

### B09. "Show history" is indented (Low)
- **Where:** Activity.tsx `.linklike` button (padding `0 12px 6px`).
- **Fix:** `.details .linklike { padding: 0; }` (or set `button.linklike { padding: 0; }` globally and
  check the sign-in "Forgot your password?" link still looks right).

### B10. Whole page flashes "Loading…" on every navigation and after every action (Medium)
- **Where:** Browser.tsx:665 returns only "Loading…"; App.tsx remounts Browser with
  `key={`${folderId}:${reload}`}`.
- **Fix:** In Browser, show "Loading…" only before the first successful load; on later loads keep the
  current content and set `aria-busy="true"` on the list with a 2 px progress bar on top of it. Keep
  the remount; this item only changes what is shown while loading.

### B11. Error page is a dead end and says "no access" for missing folders (Medium)
- **Where:** Browser.tsx 677-686.
- **Fix:** Show an h1 ("Folder not available"), the text ("You do not have access to this folder." or,
  for 404, "This folder no longer exists."), and two buttons: "Go to My files" (first root) and "Back"
  (`history.back()`).

### B12. Compact list toggle shows no pressed state (Low)
- **Where:** Browser.tsx:832, no CSS for `aria-pressed`.
- **Fix:** `button[aria-pressed="true"] { background: var(--sel); border-color: var(--primary);
  color: var(--primary-strong); }`.

### B13. Upload progress rows look unfinished (Low)
- **Where:** `.uploads` list in Browser.tsx.
- **Fix:** Style each row as a compact card (surface, border, radius, padding 8px 12px), name with
  ellipsis, progress 160 px, status in `.muted`, icon-only dismiss and cancel buttons
  (`button.iconbtn`).

### B14. Share and Privacy only work on the folder you are in (Medium)
- **Where:** Browser.tsx toolbar; details panel has no Share/Privacy for a selected folder.
- **Problem:** To share a subfolder you must open it first; selecting it shows no Share action.
- **Fix:** When the single selected item is a folder that `can_manage` (fetch `api.node(id)` for
  `shared_with` like the toolbar does), add "Share…" and "Privacy…" to the details panel, opening the
  existing dialogs with that folder's id and name.

### B15. Double-click on a row does nothing (Low)
- **Where:** Browser.tsx row handlers.
- **Fix:** `onDoubleClick`: open folder or viewable file (same as Enter).

### B16. Long names wrap under the icon (Low)
- **Where:** list name cell.
- **Fix:** Wrap icon + name in a `span.name-cell { display: inline-flex; gap: 10px; align-items:
  flex-start; }` with the icon `flex: none`, so wrapped text stays to the right of the icon.

---

## D. Dialogs

### D01. Folder picker (Move, Copy, Restore to) is centered and hard to read (High)
- **Where:** Dialogs.tsx `TargetDialog`, `.picker button` in styles.css.
- **Problem:** Folder names are centered (the global button rule `justify-content: center` beats
  `text-align: left`), have no icons, the chosen destination is not highlighted, only its name is
  shown, and "↑ Other areas…" uses an up arrow for something that is not "up".
- **Fix:** `.picker button { justify-content: flex-start; }`; add the `folder` icon before each name
  and `chev` after it; show the destination as a path ("My files › Taxes › 2025") built from the
  nodes visited; rename the first item to "Other places (My files, Server files)…" without the arrow.

### D02. Dialog footers are inconsistent (Low)
- **Where:** Preview settings has a Close button; Account and AI settings have only the ×; the Recent
  actions detail has Close.
- **Fix:** Give Account and AI settings the same footer as Preview settings (`.actions` with one
  "Close" button).

### D03. Fieldsets use the browser's default sunken border (Low)
- **Where:** Privacy dialog ("Label for this folder…"), Account ("Confirm it is you with"), any
  `fieldset`.
- **Fix:** `fieldset { border: 1px solid var(--line); border-radius: var(--r-m); padding: 8px 14px;
  margin: 12px 0; } legend { font-weight: 700; font-size: 0.9rem; padding: 0 4px; }`. In Account, when
  only one proof option exists, show it as text ("Confirm with your password") instead of a single
  radio button.

### D04. Share dialog details (Low)
- **Where:** Share.tsx.
- **Fix:** Placeholder "Name or email" on the Add people field; the "Everything inside is shared too"
  box uses the warning (orange) style for neutral information: use `.banner.info`.

---

## S. Shared files pages

### S01. "Shared by you" is a placeholder page in the main navigation (Medium)
- **Where:** SharedPages.tsx `SharedByYouPage`, App.tsx nav.
- **Problem:** A top-level nav item leads to "This list is not available yet."
- **Fix (until the backend list exists):** hide the nav item and route; keep the component. The
  "Shared by you" endpoint is already an open follow-up.

### S02. "Shared with you" is a plain bullet list (Low)
- **Where:** SharedPages.tsx `SharedWithYouPage`.
- **Fix:** Render as rows like the file list: `folder-shared` icon, name link, muted "from Owner";
  use `.empty` for the empty state (with G05).

---

## T. Trash and Recent actions

### T01. Trash buttons and wording (Low)
- **Where:** Trash.tsx 140-160, `rootName`.
- **Fix:** Buttons read "Empty My files trash…" / "Empty Server files trash…" (after G13), styled
  `danger`, placed in a header row to the right of the h1.

### T02. Trash error replaces the whole page (Low)
- **Where:** Trash.tsx:111.
- **Fix:** Keep the h1 and intro; show the error below with `className="error"`.

### T03. Recent actions: disabled Undo with no reason (Low)
- **Where:** Trash.tsx `RecentPage` rows.
- **Fix:** When `undone`, show a small "Undone" badge and no Undo button; when not reversible, keep
  the disabled button with `title="This action cannot be undone"`.

---

## C. Collections, Duplicates, AI suggestions

### C01. Duplicates page is unstyled (Medium)
- **Where:** Duplicates.tsx (`.dup-group` is undefined).
- **Problem:** Plain bullet lists, the name is a link and there is also a "View" link to the same file,
  "Move to trash" is an underlined link next to it.
- **Fix:** Each group a card (`.dup-group` = the `.card` style from G18, margin-bottom 12px); each
  member a row: file icon, name link, muted "1.9 KB · in Receipts", and on the right a small
  "Move to Trash" `danger` button. Remove the separate "View" button. Use `.empty` for "No duplicates
  found".

### C02. Collections page layout (Medium)
- **Where:** Collections.tsx `CollectionsPage`.
- **Fix:** Make the create form one row: input (max-width 360px) and Create button side by side
  (`display: flex; gap: 8px; align-items: end`). Render the collection list with G18's `.plain` rows,
  each with the `layers` icon. Use `.empty` for the empty message.

### C03. AI features do not say when AI is off (Medium)
- **Where:** Organize.tsx (AI suggestions page), Browser.tsx (Suggest collections, Suggest
  organization, Suggest PDF split), Collections suggestions.
- **Problem:** With AI turned off, the page says "No suggestions are waiting" and the buttons stay
  enabled and then fail.
- **Fix:** Load `api.aiStatus()` once in App and pass a boolean down (or a small hook). When AI is off:
  a `.banner.info` on AI suggestions ("AI is turned off on this server." + for admins a link "Turn it
  on in Admin"), and the three Browser buttons disabled with `title` "AI is turned off".

---

## Q. Search

### Q01. Search results are hard to scan (Medium)
- **Where:** Search.tsx results list.
- **Problem:** No file icon, no location (only a "Show in folder" link on its own line), a tiny grey
  match tag, long two-line snippets with no highlighting.
- **Fix:** Row layout: icon, name link, `.badge` with the match label, then muted "in <parent>" as the
  Show-in-folder link on the same line; snippet clamped to two lines (`-webkit-line-clamp: 2`) with
  the query words wrapped in `<mark>` (split on whitespace, case-insensitive, escape regex).

### Q02. Ask form misaligned (Low)
- **Where:** Ask.tsx form, `.ask form` CSS.
- **Fix:** After G09 the heights match; also give both labels `margin: 0` inside `.ask form`, make Ask
  primary (G10), and start the question empty (see N02).

### Q03. `.toplink` reused outside the top bar (Low)
- **Where:** Search.tsx and Ask.tsx "Show in folder" links use `className="toplink"`, whose styles only
  apply inside `.topbar`.
- **Fix:** Use a new `className="sublink"` (`margin-left: 8px; font-size: 0.9rem;`).

---

## V. Viewer

### V01. Title is not centered with the Back and Download buttons (Low)
- **Where:** Viewer.tsx `.viewer-bar`, `.viewer-title`.
- **Fix:** `.viewer-title { line-height: 40px; }` (button height) so it sits on the same line center.

### V02. Two Download buttons when a file cannot be shown (Low)
- **Where:** Viewer.tsx (bar Download plus the note card's Download).
- **Fix:** Hide the bar's Download while the "This file cannot be shown" card is displayed.

### V03. Viewer has no location (Low)
- **Where:** Viewer.tsx header.
- **Fix:** Under the title, a muted "in <parent folder>" link to the parent folder (node has
  `parent_id`; fetch its name).

---

## A. Administration

### A01. Admin › AI opens a big dialog while every other admin card opens a page (Medium)
- **Where:** AdminPage.tsx (`onAi` opens `AiSettings`), App.tsx.
- **Problem:** The AI card opens a long modal that also mixes personal settings ("My files", "Do not
  use my files for meaning-based search") with server model setup.
- **Fix:** Add `/admin/ai` route rendering the same `AiSettings` content as a page (reuse the
  component with an `asPage` prop that renders a `<section>` with h1 instead of `Modal`). Point the
  Admin card at it. The profile menu "AI settings" keeps the dialog. (Bigger than most items; give it
  a Sonnet thread.)

### A02. Server settings has three Save buttons for three single fields (Medium)
- **Where:** ServerSettings.tsx.
- **Fix:** One form, one "Save changes" primary button at the bottom, saving only changed values (the
  three existing API calls). Narrow fields per G08; hints in `.muted` (G01).

### A03. Page heading sizes differ (Low)
- **Where:** styles.css `.users h1` and `.backups h1` set 1.4rem; other pages use the 1.6rem `h1`.
- **Fix:** Delete the two overrides.

### A04. Usage page polish (Low)
- **Where:** UsagePage.tsx.
- **Fix:** Show the month as "November 2026" (`Intl.DateTimeFormat(undefined, {month: "long", year:
  "numeric"})`), make "Administration, AI" a link to the AI page, use `.empty` for "No models yet".

### A05. Admin cards: Usage shares the AI icon and comes before AI (Low)
- **Where:** AdminPage.tsx cards.
- **Fix:** Order: Server settings, Users, AI, Usage, Mail server, Backups. Give Usage the `activity`
  icon.

---

## U. Users

### U01. Row actions stack vertically and overflow (High)
- **Where:** Users.tsx 250-355, `.users .models-table td:last-child`.
- **Problem:** Four buttons stack, making each row about 180 px tall; "Sign out everywhere" sticks out
  of the table; "Disable" and "Make admin" look as harmless as "Reset link" and act without a confirm.
- **Fix:** Keep one visible button per row ("Reset link" or "New invitation link") and move the rest
  into a "More" `MenuButton` (from B01a; if B01a is not merged yet, a simple ProfileMenu-style
  popover): Sign out everywhere, Make admin / Make member, Disable / Enable (danger). Confirm Disable
  and role changes with `ConfirmDialog` (G07a) or `window.confirm` if G07a is not merged yet.

### U02. Accounts list is below a mail status block (Medium)
- **Where:** Users.tsx section order.
- **Fix:** Put "Accounts" first. Shrink "Mail and single sign-on" into one `.banner.info` line under
  the h1 ("Email is not set up, so copy invitation links yourself. Set up mail") with a link to the
  mail page, and drop the separate heading.

### U03. Users on a phone: rows fall apart (Medium)
- **Where:** styles.css `@media (max-width: 700px)` `.users table` rules.
- **Problem:** Each cell becomes its own bordered line without labels; the name underline covers only
  part of the width; people are hard to tell apart.
- **Fix:** At ≤700 px make each `tr` a card (`border: 1px solid var(--line); border-radius:
  var(--r-l); margin-bottom: 12px; padding: 8px 12px`), cells without borders, the role and status on
  one line (`display: inline-block` with a `·` separator), actions row at the bottom.

---

## K. Backups

### K01. One very long page mixing everything (Medium)
- **Where:** Backups.tsx, BackupJobs.tsx.
- **Problem:** About 2000 px of settings, space facts, folder table, jobs, personal key and restore,
  separated only by headings.
- **Fix:** Wrap each section (Backup settings, Space, Folders, Backup jobs, Your backup key, Restore)
  in a `.card` (G18) with 20 px gaps, and add a small in-page index of links at the top.

### K02. Folder table shows "A personal folder" four times (Medium, done: owner decision 2026-11-08)
- **Where:** Backups.tsx folders table.
- **Problem:** The admin cannot tell which person's folder each row is.
- **Fix:** Owner to decide whether admins may see owners' names here (admins already see names under
  Users; folder contents stay hidden). If yes: "Personal folder of Bob Martin" (needs the owner name
  in the API response). If no: number them ("Personal folder 1…") and say why in the hint.

### K03. No backup folder: form stays editable (Low)
- **Where:** Backups.tsx warnings list.
- **Fix:** Show the "No backup folder is mounted…" message as a `.banner.warn` at the top and wrap the
  settings form in `<fieldset disabled>` until a folder is available.

---

## M. Mail

### M01. Inbox-off message points to the wrong setup step (Low)
- **Where:** App.tsx:797-799 ("open Admin and go to Step 5: Incoming mail"); App.tsx:242 comment.
- **Fix:** "To set it up, open Admin › Mail server and turn on incoming mail." linking to the mailbox
  admin page. Fix the comment to "Step 3".

---

## P. Personal settings

### P01. Personal settings are spread over three dialogs (Low, owner decision)
- **Where:** profile menu: Account, Preview settings, AI settings.
- **Fix (proposal):** one "Settings" dialog with three sections (Account, Previews, AI) and one menu
  item. Owner to confirm before implementing.

---

## R. Phone and narrow screens

### R01. Navigation becomes three rows and hides items (High)
- **Where:** styles.css `@media (max-width: 760px)` `.sidenav` rules.
- **Problem:** The side navigation turns into about 130 px of wrapped links above every page, and the
  last row scrolls sideways with no hint, hiding Duplicates and AI suggestions.
- **Fix:** At ≤760 px hide `.sidenav` and add a "Menu" `iconbtn` (`menu` icon) at the left of the top
  bar that opens the same nav as a left drawer (`position: fixed`, full height, 280 px, scrim,
  Escape and outside click close, closes on navigation).

### R02. Selected item details are far below the list on a phone (Medium)
- **Where:** Browser.tsx details `aside` (moves under the list at ≤760 px).
- **Fix:** At ≤760 px, when one or more items are checked, show a sticky bottom bar with the main
  actions (Open, Download, Move to Trash, and "More" that scrolls the details panel into view).

The phone toolbar is covered by B01; checked pages have no sideways page scroll at 390 px.

---

## X. First-time setup (light pass)

### X01. Intro contradicts the required step (Low)
- **Where:** AdminPage.tsx intro ("Each step is optional", "finishing or skipping it completes setup")
  while Step 5 has no Skip and a red asterisk.
- **Fix:** "Steps 1 to 4 are optional and can be changed later under Admin. Step 5, the Server files,
  is required and completes setup."

### X02. Unexplained red asterisk (Low)
- **Where:** `.step-required` on Step 5.
- **Fix:** Replace the bare `*` with a small "Required" badge (`.badge`, `--danger` color via G11).

---

## Needs an owner decision (not for fix threads yet)

- **P01** merging the three personal settings dialogs.
- **Account has no way to change your own name or password** (only admin reset links). This needs a
  backend endpoint, so it is a feature, not a UI fix.
- **Share on the Server files top folder** (button shows "Share (3)") — Server files are already
  visible to everyone; decide whether sharing the top folder should be offered at all.

## ID list

| ID | Sev | Title |
|----|-----|-------|
| G01 | High | Define the missing `.muted` style |
| G02 | High | Stop typed text and placeholders being bold |
| G03 | Med | Give every error message the error style |
| G04 | Low | One style for success messages |
| G05 | Med | Use the designed empty state everywhere |
| G06 | Med | One loading indicator |
| G07a | Med | ConfirmDialog component; replace window.confirm in Browser, Duplicates, Collections |
| G07b | Med | Replace window.confirm in AiSettings, Backups, BackupJobs, MailPages |
| G08 | Med | Keep form fields to a sensible width |
| G09 | Low | Make selects match inputs |
| G10 | Low | Consistent primary buttons |
| G11 | Med | Bell badge dark-mode contrast and hard-coded colors |
| G12 | Low | Merge duplicate CSS rules |
| G13 | Low | Wording fixes (Trash, Server files, Lock, Mail server) |
| G14 | Med | Remove "AI is optional" copy |
| G15 | Low | Curly quotes everywhere |
| G16 | Low | Icon close buttons with one name |
| G17 | Low | Hide empty message lines |
| G18 | Med | Define `.plain`, `.card`, `.panel` |
| N01 | Med | Regroup the side navigation |
| N02 | High | Add an "Ask AI" page and nav item |
| N03 | Low | Search icon inside the search field |
| N04 | Low | Own icon for Account |
| N05 | Med | Profile menu alignment and keyboard support |
| N06 | Low | Notification panel header and rows |
| N07 | Low | Admin breadcrumb and Admin highlight on Backups |
| N08 | Low | Style boot error, no-locations message, skip link |
| B01a | High | Upload menu button |
| B01b | High | "More" overflow menu in the folder toolbar |
| B02 | High | Short, one-line Modified dates |
| B03 | Med | Folder name as page title |
| B04 | High | Show every action result in the toast |
| B05 | Med | No underline on file names |
| B06 | Med | Group and style the details panel actions |
| B07 | Med | Fixed column widths in the file list |
| B08 | Low | Status flags as badges |
| B09 | Low | Align "Show history" |
| B10 | Med | Keep content while a folder reloads |
| B11 | Med | Helpful folder error page |
| B12 | Low | Pressed style for Compact list |
| B13 | Low | Style upload progress rows |
| B14 | Med | Share and Privacy for a selected subfolder |
| B15 | Low | Double-click opens |
| B16 | Low | Long names keep the icon column |
| D01 | High | Fix the folder picker |
| D02 | Low | Close footer on Account and AI settings |
| D03 | Low | Style fieldsets; single proof option as text |
| D04 | Low | Share dialog placeholder and info style |
| S01 | Med | Hide "Shared by you" until it works |
| S02 | Low | Shared with you as rows |
| T01 | Low | Trash empty buttons wording and place |
| T02 | Low | Trash error keeps the page |
| T03 | Low | Recent actions undo states |
| C01 | Med | Style the Duplicates page |
| C02 | Med | Collections page layout |
| C03 | Med | Say when AI is off |
| Q01 | Med | Scannable search results |
| Q02 | Low | Align the Ask form |
| Q03 | Low | Own class for "Show in folder" links |
| V01 | Low | Center the viewer title |
| V02 | Low | One Download button for unviewable files |
| V03 | Low | Folder link in the viewer |
| A01 | Med | Admin › AI as a page (Sonnet) |
| A02 | Med | One Save on Server settings |
| A03 | Low | Same h1 size on Users and Backups |
| A04 | Low | Usage page polish |
| A05 | Low | Admin card order and Usage icon |
| U01 | High | Users row actions in a menu |
| U02 | Med | Accounts list first on Users |
| U03 | Med | Users as cards on a phone |
| K01 | Med | Backups sections as cards |
| K03 | Low | Backups: disable the form without a backup folder |
| M01 | Low | Inbox-off message points to Mail server |
| R01 | High | Phone navigation drawer |
| R02 | Med | Phone action bar for selected items |
| X01 | Low | Setup intro copy |
| X02 | Low | "Required" badge on Step 5 |
