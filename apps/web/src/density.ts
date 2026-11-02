// List density (M21b): comfortable by default, compact on request. A per-device preference only;
// it is applied to the page root and kept in localStorage when the browser allows it.
export type Density = "comfortable" | "compact";

const KEY = "filecanopy.density";

export function loadDensity(): Density {
  try {
    return localStorage.getItem(KEY) === "compact" ? "compact" : "comfortable";
  } catch {
    return "comfortable";
  }
}

export function applyDensity(d: Density): void {
  if (d === "compact") document.documentElement.dataset.density = "compact";
  else delete document.documentElement.dataset.density;
}

export function saveDensity(d: Density): void {
  applyDensity(d);
  try {
    localStorage.setItem(KEY, d);
  } catch {
    // private mode or blocked storage: the choice just lasts until the page is reloaded
  }
}
