# Inbound mailbox (optional)

FileCanopy can read one mailbox over IMAP so people can email documents to it. In this version
(M13) the mailbox is a **foundation**: messages are read, parsed, matched to a person or held in a
quarantine, and listed. Nothing is filed into anyone's folders yet (that is the next milestone).
FileCanopy works fully without a mailbox.

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
| `FILECANOPY_IMAP_AUTH_POLICY` | `aligned` (default) or `dmarc`. |
| `FILECANOPY_IMAP_RECIPIENT_HEADERS` | Headers that carry the real recipient. Default `x-original-to,delivered-to`. |
| `FILECANOPY_IMAP_POLL_SECONDS` | How often to look (default 120). |

FileCanopy only reads: it never deletes, moves or flags messages in the mailbox.

## Finding your authserv-id

Send yourself a message, open "show original" / "view source" and find the header that starts
with `Authentication-Results:`. The first word after the colon (up to the first `;`) is the
authserv-id, for example `mx.example.org`. Use that exact value. If your provider adds no such
header, every message is held in quarantine; that is the safe result, not a fault.

## How a message is decided

A message is recognised only when **all** of these are true:

1. The From address belongs to exactly one active account (its sign-in email or an extra address
   the person confirmed).
2. Your provider's own `Authentication-Results` header (the topmost one, with your authserv-id)
   shows the sender passed DMARC for the From domain. With the default `aligned` policy, DKIM and
   SPF both passing for exactly the From domain also counts.
3. The recipient header added by your provider names exactly one of the two intake addresses.
   The visible `To:` and `Cc:` lines are never used.

Anything else is **held**, with a reason, for an administrator: unknown or ambiguous sender,
failed or missing sender check, forwarded mail, oversized or odd messages. Held mail is not
given to any person or AI service. Attachments are kept for 30 days in a private folder under
app-data (not in the library), then deleted; the record stays.

## Limits

Defaults: 25 MiB per message, 200 parts, depth 6, 50 attachments, 50 messages per check. Larger
messages are held without being downloaded.

## Things to know

- Forwarded mail usually fails the sender check and is held.
- A provider that does not add the recipient header (some catch-all setups) holds everything;
  use a provider or alias setup that does.
- Administrators see held mail but never a person's recognised mail.
