# AI organization suggestions

Optional, and off until you turn it on. It needs AI to be set up first (see `ai.md`). Design and
threat model: `docs/milestones/M16-design-and-plan.md`.

## What it does

Select a file and choose **Suggest organization**. The server sends that one file's name, size and
extracted text, plus a list of folder names you could move it into, to your AI provider, and gets
back a suggested name, a folder (or a new one), and a note if the file looks sensitive. You see the
suggestion under **AI suggestions** and press **Apply** or **Dismiss**. Nothing changes before
that, and the server checks the file again when you press Apply: if it changed, nothing happens.

## What it never does

- Change who can see a file, lower a privacy label, replace a file or delete.
- Look at Private or Confidential files. They are not sent to any AI, cloud or local, in this
  version.
- Offer Private or Confidential folders as places to move to, or send their names.
- Rename a file to a different kind (the extension stays).
- Rename or move a file that looks sensitive (card or identity numbers, keys, passwords, medical
  words); you get a warning instead.

## Modes

Each person chooses for their own personal files; the administrator chooses for the shared files.

- **Off** (default).
- **Suggest, and I decide**: suggestions wait for you.
- **Automatic for the safest ones**: needs the administrator to set a confidence bar first. A
  suggestion is applied by itself only if it is a rename or move into an existing folder you can
  already write to, nothing blocks it (text was readable, nobody gains access, not sensitive), and
  the AI's own confidence is at or above the bar. Creating a folder is always your decision.
  Applied changes show under **AI suggestions** with **Undo**.

## Choosing the bar

There is no built-in number, because a model's confidence is its own guess. In the AI dialog the
administrator sees, for each confidence band, how many suggestions people applied by hand, how many
were done automatically and how many were dismissed (counts only). Start with review mode, let
people use it, then set the bar just above the band where people started dismissing suggestions.
Raise it if automatic changes are being undone. Leave it empty to keep automatic changes off.

## Cost

Each suggestion is one request on your AI provider and counts against the monthly limit (see
`ai.md`). A batch of up to 20 stops as soon as the AI layer refuses (for example the limit is
reached).
