import { ICON_PATHS, type IconName } from "./iconPaths";

// Decorative inline icon: the markup is a fixed constant from iconPaths.ts, never user input.
// Meaning always comes from the visible text next to it (or an aria-label on the button).
export function Icon({ name, size }: { name: IconName; size?: number }) {
  return (
    <svg
      className="i"
      viewBox="0 0 24 24"
      width={size}
      height={size}
      aria-hidden="true"
      focusable="false"
      dangerouslySetInnerHTML={{ __html: ICON_PATHS[name] }}
    />
  );
}

// Logo A: a leafy canopy over a folder (docs/design/brand.html). Colours come from CSS variables
// so it follows the light and dark themes.
export function Logo({ size = 30 }: { size?: number }) {
  return (
    <svg
      className="logo"
      viewBox="0 0 64 64"
      width={size}
      height={size}
      aria-hidden="true"
      focusable="false"
    >
      <rect x="7" y="27" width="50" height="29" rx="7" className="logo-body" />
      <g className="logo-leaf">
        <circle cx="32" cy="17" r="11" />
        <circle cx="19.5" cy="23.5" r="8" />
        <circle cx="44.5" cy="23.5" r="8" />
        <rect x="11.5" y="22" width="41" height="9.5" rx="4.75" />
      </g>
      <path
        d="M22 41h20"
        className="logo-line"
        strokeWidth="3.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function Wordmark({ size = 30 }: { size?: number }) {
  return (
    <span className="wordmark">
      <Logo size={size} />
      <span>
        File<span className="c">Canopy</span>
      </span>
    </span>
  );
}
