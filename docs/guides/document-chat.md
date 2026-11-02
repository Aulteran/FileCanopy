# Asking questions about your files

Optional. On the search results page, "Ask about your files" answers a question from the text of
your files and shows which files it used. Design and threat model:
`docs/milestones/M20-design-and-plan.md`.

## What you need

Nothing for the passages: without AI, the panel still shows the matching passages from your files.
For a written answer, set up an AI provider (see `docs/guides/ai.md`). Meaning-based search
(`docs/guides/semantic-search.md`) makes retrieval better but is not required.

## What it does and does not do

- It answers from the passages it found and numbers them. Click a source to open the file.
- If the files do not clearly answer, no answer is shown, only the matching passages.
- It never changes anything. It cannot move, rename, delete, share or relabel a file.
- Nothing is saved. The question and answer are gone when you leave the page.
- It looks in **My files** or **Shared files**, one at a time.

## Privacy

- A **cloud** AI service sees passages from Normal files only. Private and Confidential files are
  never sent to it, even if you ask about them; the panel tells you how many matching files were
  kept back.
- A **local** AI model (on your own network) can read Private files if you turned on local
  processing for your files. Confidential files are never used.
- Access is checked for every file at the moment you ask, and again after the answer is made.

## Limits

- 10 questions a minute per person, up to 6 passages per question.
- The monthly AI budget applies. When it is reached, you still get the passages.
- Citations name the file and show the passage; page numbers are not available yet.
- Totals are quoted from the text, not calculated.

Settings (administrators, environment variables): `FILECANOPY_CHAT_PER_MINUTE` (10),
`FILECANOPY_CHAT_MAX_SOURCES` (6), `FILECANOPY_CHAT_EXCERPT_CHARS` (1200).
