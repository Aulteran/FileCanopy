# First-run setup

1. **Enter the setup token.** The first visit asks for a one-time setup token. If you did not set `FILECANOPY_SETUP_TOKEN`, the API prints a token to its log. Show it with:

   ```bash
   docker compose logs api | grep "SETUP TOKEN"
   ```

   If you set your own token, use that value instead. A restart can issue a new generated token, so check the log again if the old one stops working.
2. **Create the administrator account.** This is the first account, and it is the only one that can be created without an invitation.
3. **Optionally import existing files.** While the setup window is open, you can import an existing folder or zip into the shared folder or your personal folder. The window closes once you add content by hand, finish setup, or skip it.
