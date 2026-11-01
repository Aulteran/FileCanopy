// The result of a provider round trip arrives as "/?sso=<code>" (a redirect from the API). It is
// read once and removed from the address bar so a reload or a copied URL does not repeat it.
// "signed_in" and "welcome" mean a session now exists; "linked" means the account got a link;
// anything else is an error code that format.ts knows how to explain.

let taken: string | null | undefined;

export function takeSsoResult(): string | null {
  if (taken === undefined) {
    const code = new URLSearchParams(window.location.search).get("sso");
    taken = code && /^[A-Za-z_]{1,40}$/.test(code) ? code : null;
    if (code !== null) window.history.replaceState(null, "", "/");
  }
  return taken;
}

const OK = new Set(["signed_in", "welcome", "linked", "reauthed"]);

export function ssoIsError(code: string | null): code is string {
  return code !== null && !OK.has(code);
}
