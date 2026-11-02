# Collections

A collection is a named group of files you choose, like a playlist. It does not move or copy
anything: the files stay in their folders, and a file can be in several collections. Design and
threat model: `docs/milestones/M18-design-and-plan.md`.

## Using collections

- **Collections** in the sidebar lists yours and the ones shared with you. Create one by typing a
  name, or select a file and choose **Add to collection…**.
- Renaming or moving a file keeps it in its collections. A file in the trash is hidden from them
  and comes back when you restore it. Deleting a collection never deletes files.
- Each file shows where it actually lives, with a link to its folder.

## Who sees what

- A collection is private to you until you share it. Sharing gives a person the collection's name
  and the files in it **that they can already open**. It never gives access to a file. Others see
  only their own count, never how many files they cannot see.
- Viewers look; editors can also add and remove files they can open. Only you rename, share or
  delete. A person can leave a collection shared with them.
- A file in a locked Confidential folder is hidden from the collection (and not counted) until the
  folder is unlocked. Revoking someone's access to a file takes effect immediately.

## AI suggestions (optional)

Collections work without AI. If AI is set up for your files (see `ai.md`), **Suggest collections**
in a folder sends the names and opening text of up to 20 files listed there and asks which belong
together. Suggestions appear under **Collections** and **AI suggestions**. Nothing is created until
you press **Create collection**; you can rename it and untick files first, or dismiss it.

Private and Confidential files, and files in someone else's personal folder, are never sent.
Suggestions that no longer fit (a file moved out of reach, became protected, or a week passed) are
removed.
