import type { ReactNode } from "react";

// Lucide-style icons from the approved design (docs/design/screens.html), as plain JSX so nothing
// is fetched and no HTML is injected.
export const ICON_PATHS = {
  folder: (
    <>
      <path
        d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2.2h7.5A2.5 2.5 0 0 1 21 9.7v7.8a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
      />
    </>
  ),
  "folder-user": (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2.2h7.5A2.5 2.5 0 0 1 21 9.7v7.8a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z" />
        <circle cx="12" cy="12.2" r="1.8" />
        <path d="M8.8 17a3.4 3.4 0 0 1 6.4 0" />
      </g>
    </>
  ),
  "folder-shared": (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2.2h7.5A2.5 2.5 0 0 1 21 9.7v7.8a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z" />
        <circle cx="10" cy="12.3" r="1.6" />
        <circle cx="14.6" cy="12.3" r="1.6" />
        <path d="M7.3 17a2.8 2.8 0 0 1 5.4 0M11.9 17a2.8 2.8 0 0 1 5.4 0" />
      </g>
    </>
  ),
  file: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <path d="M14 3H7.5A2.5 2.5 0 0 0 5 5.5v13A2.5 2.5 0 0 0 7.5 21h9a2.5 2.5 0 0 0 2.5-2.5V8z" />
        <path d="M14 3v5h5M9 13h6M9 17h4" />
      </g>
    </>
  ),
  sheet: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <rect x="4" y="3.5" width="16" height="17" rx="2.5" />
        <path d="M4 9.5h16M4 15h16M10 9.5v11" />
      </g>
    </>
  ),
  image: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <rect x="3.5" y="4.5" width="17" height="15" rx="2.5" />
        <circle cx="9" cy="10" r="1.6" />
        <path d="M20 16l-5-5-8 8.5" />
      </g>
    </>
  ),
  upload: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <path d="M12 15V4M7.5 8.5 12 4l4.5 4.5M4 15v3.5A2.5 2.5 0 0 0 6.5 21h11a2.5 2.5 0 0 0 2.5-2.5V15" />
      </g>
    </>
  ),
  download: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M4 20h16" />
      </g>
    </>
  ),
  share: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <circle cx="9" cy="8" r="3.2" />
        <path d="M3 20a6 6 0 0 1 12 0M18.5 8v6M15.5 11h6" />
      </g>
    </>
  ),
  users: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <circle cx="9" cy="8" r="3.2" />
        <path d="M3 20a6 6 0 0 1 12 0" />
        <circle cx="17" cy="9" r="2.5" />
        <path d="M16.5 14.2A5 5 0 0 1 21 19" />
      </g>
    </>
  ),
  move: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2.2h7.5A2.5 2.5 0 0 1 21 9.7v7.8a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z" />
        <path d="M9 13.5h6M12.5 11l2.5 2.5-2.5 2.5" />
      </g>
    </>
  ),
  copy: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <rect x="8.5" y="8.5" width="12" height="12" rx="2.5" />
        <path d="M15.5 8.5V6A2.5 2.5 0 0 0 13 3.5H6A2.5 2.5 0 0 0 3.5 6v7A2.5 2.5 0 0 0 6 15.5h2.5" />
      </g>
    </>
  ),
  trash: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <path d="M4 7h16M9.5 7V4.5h5V7M6 7l1 12.5A1.6 1.6 0 0 0 8.6 21h6.8a1.6 1.6 0 0 0 1.6-1.5L18 7M10 11v6M14 11v6" />
      </g>
    </>
  ),
  undo: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <path d="M9 14 4 9l5-5" />
        <path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11" />
      </g>
    </>
  ),
  restore: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1L3.5 8.5" />
        <path d="M3.5 4v4.5H8M12 8v4l3 2" />
      </g>
    </>
  ),
  lock: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <rect x="5" y="10.5" width="14" height="10" rx="2.5" />
        <path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5M12 14.5v2" />
      </g>
    </>
  ),
  unlock: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <rect x="5" y="10.5" width="14" height="10" rx="2.5" />
        <path d="M8.5 10.5V8a3.5 3.5 0 0 1 6.8-1.2M12 14.5v2" />
      </g>
    </>
  ),
  "eye-off": (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <path d="M3 3l18 18M10.6 6.1A9.6 9.6 0 0 1 12 6c5 0 8.5 4.5 9 6-.3.8-1.2 2.3-2.7 3.6M6.2 7.8C4.4 9 3.3 10.7 3 12c.5 1.5 4 6 9 6 1.4 0 2.7-.4 3.8-.9M9.9 10a3 3 0 0 0 4.1 4.1" />
      </g>
    </>
  ),
  backup: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <rect x="3" y="4" width="18" height="5" rx="1.5" />
        <path d="M5 9v9.5A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5V9M10 13h4" />
      </g>
    </>
  ),
  activity: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <circle cx="12" cy="12" r="8.5" />
        <path d="M12 7.5V12l3 2" />
      </g>
    </>
  ),
  settings: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <path d="M4 6.5h9M17 6.5h3M4 12h3M11 12h9M4 17.5h11M19 17.5h1" />
        <circle cx="15" cy="6.5" r="2" />
        <circle cx="9" cy="12" r="2" />
        <circle cx="17" cy="17.5" r="2" />
      </g>
    </>
  ),
  storage: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <path d="M3.5 13.5 6 5.5A1.5 1.5 0 0 1 7.4 4.5h9.2A1.5 1.5 0 0 1 18 5.5l2.5 8" />
        <rect x="3.5" y="13.5" width="17" height="6.5" rx="2" />
        <path d="M16.5 16.8h.01M13.5 16.8h.01" />
      </g>
    </>
  ),
  search: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
      >
        <circle cx="11" cy="11" r="6.5" />
        <path d="m20 20-4.3-4.3" />
      </g>
    </>
  ),
  check: (
    <>
      <path
        d="m5 12.5 4.5 4.5L19 7.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </>
  ),
  alert: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <path d="M10.3 4.3 2.8 17.5A2 2 0 0 0 4.5 20.5h15a2 2 0 0 0 1.7-3L13.7 4.3a2 2 0 0 0-3.4 0z" />
        <path d="M12 10v4M12 17.2h.01" />
      </g>
    </>
  ),
  info: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
      >
        <circle cx="12" cy="12" r="8.5" />
        <path d="M12 11v5.5M12 7.8h.01" />
      </g>
    </>
  ),
  more: (
    <>
      <g fill="currentColor">
        <circle cx="6" cy="12" r="1.6" />
        <circle cx="12" cy="12" r="1.6" />
        <circle cx="18" cy="12" r="1.6" />
      </g>
    </>
  ),
  plus: (
    <>
      <path
        d="M12 5v14M5 12h14"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.9"
        strokeLinecap="round"
      />
    </>
  ),
  "folder-plus": (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2.2h7.5A2.5 2.5 0 0 1 21 9.7v7.8a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z" />
        <path d="M12 10.5v6M9 13.5h6" />
      </g>
    </>
  ),
  rename: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z" />
        <path d="m13.5 6.5 4 4" />
      </g>
    </>
  ),
  close: (
    <>
      <path
        d="M6 6l12 12M18 6 6 18"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.9"
        strokeLinecap="round"
      />
    </>
  ),
  chev: (
    <>
      <path
        d="m9 6 6 6-6 6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.9"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </>
  ),
  back: (
    <>
      <path
        d="m15 6-6 6 6 6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.9"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </>
  ),
  menu: (
    <>
      <path
        d="M4 7h16M4 12h16M4 17h16"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.9"
        strokeLinecap="round"
      />
    </>
  ),
  list: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
      >
        <path d="M9 6.5h11M9 12h11M9 17.5h11" />
        <path d="M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01" strokeWidth="2.6" />
      </g>
    </>
  ),
  rows: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
      >
        <path d="M4 5h16M4 9h16M4 13h16M4 17h16M4 21h16" />
      </g>
    </>
  ),
  key: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <circle cx="8" cy="15" r="4" />
        <path d="m11 12 8.5-8.5M16 7l2.5 2.5M14 9l2 2" />
      </g>
    </>
  ),
  link: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1" />
        <path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" />
      </g>
    </>
  ),
  zip: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <path d="M14 3H7.5A2.5 2.5 0 0 0 5 5.5v13A2.5 2.5 0 0 0 7.5 21h9a2.5 2.5 0 0 0 2.5-2.5V8z" />
        <path d="M11 3v2h2v2h-2v2h2v2h-2M11 14.5h2v3h-2z" />
      </g>
    </>
  ),
  sprout: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <path d="M12 21v-9" />
        <path d="M12 12C12 7.5 8.5 5 4 5c0 4.5 3.5 7 8 7zM12 14c0-3.5 2.8-6 7-6 0 3.5-2.8 6-7 6z" />
      </g>
    </>
  ),
  refresh: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <path d="M20 11a8 8 0 0 0-14.3-4.9L4 8M4 4v4h4M4 13a8 8 0 0 0 14.3 4.9L20 16M20 20v-4h-4" />
      </g>
    </>
  ),
  device: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <rect x="3" y="4.5" width="18" height="12" rx="2" />
        <path d="M8 20h8M12 16.5V20" />
      </g>
    </>
  ),
  phone: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <rect x="7" y="3" width="10" height="18" rx="2.5" />
        <path d="M11 17.5h2" />
      </g>
    </>
  ),
  bell: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 2h-15zM10 21h4" />
      </g>
    </>
  ),
  mail: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <rect x="3" y="5.5" width="18" height="13" rx="2.5" />
        <path d="m4 8 8 5.5L20 8" />
      </g>
    </>
  ),
  layers: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <path d="m12 3.5 9 4.5-9 4.5L3 8z" />
        <path d="m3 12.5 9 4.5 9-4.5M3 16.5l9 4.5 9-4.5" />
      </g>
    </>
  ),
  sparkles: (
    <>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinejoin="round"
        strokeLinecap="round"
      >
        <path d="M10 4.5 11.8 9l4.5 1.8-4.5 1.8L10 17l-1.8-4.4L3.7 10.8 8.2 9zM18 3.5v3M16.5 5h3M18 16v4M16 18h4" />
      </g>
    </>
  ),
} satisfies Record<string, ReactNode>;

export type IconName = keyof typeof ICON_PATHS;
