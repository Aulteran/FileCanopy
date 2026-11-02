# Splitting PDFs and finding duplicates

Splitting works without AI. AI, if you have set it up (see `ai.md`), only helps choose the cut
points. Design and threat model: `docs/milestones/M17-design-and-plan.md`.

## Splitting a scanned or combined PDF

Select a PDF and choose **Suggest PDF split**. The server looks at each page and proposes where one
document ends and the next begins. The proposal appears under **AI suggestions → PDF splits** with
the page ranges and the names the new files would get. Nothing is written until you press **Split**.

How cut points are found:

- A blank page ends the document before it (the blank page stays with it).
- A page marked "Page 1 of …" after other pages, or a change in the "of …" number, starts a new
  document.
- Scanned pages without text are read by the OCR service when it is set up (up to 40 pages).
- If AI is on for the file and the PDF has at most 100 pages, the AI may suggest a better split and
  titles. It only sees the file's own page text. If its answer does not cover the pages exactly,
  the built-in rules are used and the proposal says so.

When you press **Split**:

- The new files are written next to the original. The original is never changed or removed.
- Every page ends up in exactly one new file. Each file is re-opened and compared with the original
  (page count, rotation, text of every page) before it is kept. If anything does not match, nothing
  is kept.
- Existing files are never replaced; a number is added to the name if it is taken.
- You can tick **Leave blank pages at the edges out** (they remain in the original), or type your
  own cut points ("1, 4, 9" = parts starting at pages 1, 4 and 9).
- The new files are listed in the file history. **Move the new files to the trash** on the AI
  suggestions page reverses the split; any new file you changed since stays where it is.

Not copied to the new files: bookmarks and attached files of the whole PDF (they stay in the
original; the proposal tells you when the PDF has them).

Private and Confidential files are not read for this and are never sent to AI.

## Duplicates

**Duplicates** in the sidebar lists files that are identical (same content) and files whose text is
almost the same, among files you can open. A new file appears after the background scan has reached
it. The page only reports. To get rid of a copy, press **Move to trash** on it yourself; the trash
keeps it and you can restore it. Files in Private and Confidential folders are listed only if they
are identical to another file you can open; their text is never compared.
