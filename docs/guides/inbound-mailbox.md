# Inbound mailbox (optional)

FileCanopy can read one mailbox over IMAP so people can email documents to it. Messages are read,
parsed and matched to a person or held in a quarantine. Attachments from a recognised person are
filed as if that person had uploaded them (see "Where attachments go"). FileCanopy works fully
without a mailbox.

## What you need

- A mailbox on a provider you already use, reachable over IMAP with TLS.
- Two recipient addresses that deliver into that mailbox, for example `personaldocs@example.org`
  (a person's own intake) and `ingest@example.org` (the shared intake). Aliases of one mailbox are
  fine.
- Outbound mail (SMTP) if people should add extra sender addresses; see the main README.

## Settings

Put these in `deploy/.env` (see `deploy/.env.example`):

| Setting | Meaning |
|---|---|
| `FILECANOPY_IMAP_HOST`, `_PORT`, `_SECURITY` | Server, port (993), `ssl` or `starttls`. `none` only for a mailbox on the same machine. |
| `FILECANOPY_IMAP_USERNAME`, `_PASSWORD` | Use an app password or a restricted credential, not your main password. |
| `FILECANOPY_IMAP_PERSONAL_ADDRESS`, `_INGEST_ADDRESS` | The two intake addresses. They must differ. |
| `FILECANOPY_IMAP_AUTHSERV_ID` | **Required to accept any mail.** The name your provider writes at the start of its `Authentication-Results` header. |
| `FILECANOPY_IMAP_AUTH_POLICY` | `aligned` (default) or `dmarc`. Both need an aligned DKIM pass. |
| `FILECANOPY_IMAP_RECIPIENT_HEADERS` | Headers that carry the real recipient. Default `x-original-to,delivered-to`. |
| `FILECANOPY_IMAP_POLL_SECONDS` | How often to look (default 120). |

FileCanopy only reads: it never deletes, moves or flags messages in the mailbox.

## Finding your authserv-id

Have someone **outside your mail provider** (a different provider's account) send a message to
the mailbox, open "show original" / "view source" and find the header that starts with
`Authentication-Results:`. The first word after the colon (up to the first `;`) is the
authserv-id, for example `mx.example.org`. Use that exact value. If your provider adds no such
header, every message is held in quarantine; that is the safe result, not a fault.

**Check the other paths too.** Many providers skip `Authentication-Results` for mail between two
accounts on the same provider. If it is missing there, a sender on your provider can write a fake
header carrying your authserv-id and FileCanopy cannot tell it apart. Send a test from another
account on your provider and confirm it **also** carries the provider's header. Your provider must
remove forged `Authentication-Results` headers that use its own name (RFC 8601, section 5); if
you cannot confirm that, do not set `FILECANOPY_IMAP_AUTHSERV_ID` (everything is then held) or do
not let same-provider accounts send to the mailbox. The Mailbox page shows a reminder until the
first message has passed the check.

## How a message is decided

A message is recognised only when **all** of these are true:

1. The From address belongs to exactly one active account (its sign-in email or an extra address
   the person confirmed).
2. Your provider's own `Authentication-Results` header (the topmost one, with your authserv-id)
   shows a **DKIM signature from exactly the From domain** that passed, together with either a
   DMARC pass for that domain or (default `aligned` policy, no DMARC result) an SPF pass for
   exactly that domain. DMARC or SPF alone is not enough, because they can pass for mail sent
   through a shared sending service. Policy `dmarc` requires DMARC as well as the DKIM pass.
3. The recipient header added by your provider names exactly one of the two intake addresses.
   The visible `To:` and `Cc:` lines are never used.

Under the default `aligned` policy an SPF result only counts when `smtp.mailfrom` is a full
address (it contains an `@`); a bare domain does not.

Anything else is **held**, with a reason, for an administrator: unknown or ambiguous sender,
failed or missing sender check, forwarded mail, oversized or odd messages. Held mail is not
given to any person or AI service. Attachments of held mail addressed to the **shared** address are
kept for 30 days in a private folder under app-data (not in the library), then deleted; the
record stays. Held mail addressed to the personal address, or to an unknown address, keeps
metadata only (names, sizes, hashes): an administrator cannot put it in a personal folder anyway.

## Where attachments go

A recognised message waits in a queue with its attachments staged under app-data, then:

- **`personaldocs@`**: into a folder called `Email inbox` at the top of the sender's own files
  (created the first time). The sender can rename or move it; a file with that name, or a folder
  the sender cannot write to, makes filing fail and the sender is told.
- **`ingest@`**: into the **shared email folder** an administrator picks on the Mailbox page
  (open the folder in Files and paste its address). The sender must be allowed to add files there,
  exactly as in the web page. Until a folder is chosen, shared mail waits and administrators get
  a reminder; it is filed when one is chosen.

Each attachment goes through the same upload code as the web page, acting as the sender: the same
permission checks, name rules, file history (marked "email") and search indexing. Names are
cleaned; clashes become `name (2).ext`. The message body is never filed.

Before an archive reaches the library it is checked: at most 1000 entries, 1 GiB unpacked, an
expansion ratio under 100 for large entries, and no archive inside an archive (zip, Office and
OpenDocument files, gzip, bzip2, xz). Other archive formats (7z, rar, tar) are stored as plain
files. A refused attachment is not filed and the sender is told why; the rest of the message is
filed. These limits are `FILECANOPY_MAIL_ARCHIVE_MAX_ENTRIES`, `_MAX_UNCOMPRESSED_BYTES`,
`_MAX_RATIO`.

If filing fails for a reason that may pass (a busy folder, a full disk) it is retried five times
with growing delays; after that the message is marked failed, the sender gets one notice and may
press **Try again** once in the Email inbox page. Permanent problems (no permission, no shared
folder yet) fail straight away. Each person is limited to
`FILECANOPY_MAIL_DAILY_MESSAGES_PER_SENDER` (200) filed messages a day.

If an address stops belonging to the sender (removed, moved to another account, account disabled)
before filing, nothing is filed and the message is held.

## Who is told

Notices are in the app, never by email, and never contain file names, subjects or addresses.

- Personal mail: only its owner.
- Shared mail: the sender and every active administrator, as a count; an administrator sees the
  folder name only if they can open that folder.
- Administrators also get one merged "N emails wait for review" notice for held mail, and a
  reminder while shared mail waits for a folder. They are never told about a person's failures.

## Approving held mail

For a held message whose attachments were kept (shared address only), an administrator can file
the attachments into the shared email folder with their own rights, or reject the message. This
gives the unknown sender nothing: no account, no address, no access. Approval cannot place a file
in a personal folder. To have future mail from an address recognised, its owner adds it to their
account (Account, Sender addresses) and confirms it from the link we send.

## Limits

Defaults: 25 MiB per message (at most 50 MiB can be configured), 200 parts, depth 6, 50 attachments, 50 messages per check. Larger
messages are held without being downloaded.

## Things to know

- Forwarded mail usually fails the sender check and is held.
- A provider that does not add the recipient header (some catch-all setups) holds everything;
  use a provider or alias setup that does.
- Administrators see held mail but never a person's recognised mail, its failures or its files.
