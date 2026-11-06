# Security and privacy

- **Administrators cannot open personal folders.** The administrator role does not let anyone browse, search or list another person's personal files. The only way in is a grant created by the owner of that folder.
- **Passwords** are stored with Argon2id. Sign-in attempts are rate-limited, sessions are kept on the server, and cookies are HTTP-only.
- **Host access is outside the app's trust model.** Anyone who can read the server's disk can read the files in `library/`, the same as any other folder on that machine.
- **Private and Confidential** labels are implemented and inherit downward. Both block cloud AI; local processing requires opt-in, and Confidential content requires an unlock that expires after five minutes of user inactivity in that folder. This is app access control, not encryption at rest or E2E encryption. Host, hypervisor, storage and backup administrators can still access plaintext files and state.
- **External processing needs care.** Normal files may be sent to configured cloud AI after applicable consent. Raising a privacy label cannot recall a previous transmission. MCP clients can forward the Normal data they receive to another provider. No provider retention or compliance guarantee is made.
- **Backups contain protected data.** Disaster-recovery copies include all files and state. Per-folder backup keys (ADR-016, M25) are implemented but await independent security review; archives made before them are opened by the old administrator key. See [known limitations](../release/known-issues.md).
- **No external security audit** has been done yet. Security work so far has been internal reviews plus automated tests.
