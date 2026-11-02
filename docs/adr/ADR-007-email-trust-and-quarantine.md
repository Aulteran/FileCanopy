# ADR-007: Email trust and quarantine pipeline

- **Status:** Accepted (2026-11-01)
- **Date:** 2026-11-01
- **Milestone:** M13

## Context

Inbound mail is attacker-controlled. `From` is trivially forged, and any header a sender writes
(`Authentication-Results`, `X-Original-To`, `Delivered-To`) can be forged too. The product needs
"mail a verified person sends reaches their intake" without letting a stranger inject documents.

## Decision

1. An address proves nothing by itself. A message maps to an account only when (a) exactly one
   active account owns the `From` address (its primary email or a verified alias) **and** (b) the
   mailbox provider's own topmost `Authentication-Results` header (configured authserv-id) reports
   a pass for the From domain (`dmarc=pass`, or `dkim=pass` and `spf=pass` aligned exactly in the
   default `aligned` policy).
2. The destination comes from the topmost `X-Original-To`/`Delivered-To` and must equal exactly one
   configured intake address; `To`/`Cc` are never used.
3. Every other outcome quarantines with a reason code: unknown, ambiguous, failed or missing
   authentication, oversized or malformed MIME. Quarantine is admin-only staging outside the
   library; nothing in it reaches AI.
4. Identity is re-checked when a message is processed (`revalidate`), never cached from arrival.
5. Dedupe uses `(mailbox, UIDVALIDITY, UID)` and the raw-content hash, not `Message-ID`.
6. M13 persists metadata only for verified mail; delivering attachments is M14.

## Consequences

- Providers that do not add `Authentication-Results` quarantine all mail until the operator names
  the right authserv-id; a documented, safe failure.
- Forwarded mail usually quarantines.
- The model trusts the mailbox provider for SPF/DKIM/DMARC; the app does not validate them itself.

## Alternatives considered

- Validating SPF/DKIM in-process: needs DNS egress and gets forwarding wrong; rejected for now.
- Trusting `From` plus a shared secret in the subject: weak and leaks into mail clients.
- Dedupe by `Message-ID`: lets an attacker suppress a real message by replaying its id.

## Security and privacy impact

Reduces the forged-sender risk to the provider's authentication quality and stores no message
bodies. Administrators can read quarantined (unaccepted) mail but not verified personal mail.
