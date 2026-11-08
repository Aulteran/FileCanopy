import { useState, type ReactNode } from "react";
import { AccountDialog } from "./Account";
import { AiSettings } from "./AiSettings";
import { Modal } from "./Dialogs";
import { PreviewSettings } from "./PreviewSettings";

export type SettingsTab = "account" | "previews" | "ai";

const TABS: { id: SettingsTab; label: string }[] = [
  { id: "account", label: "Account" },
  { id: "previews", label: "Previews" },
  { id: "ai", label: "AI" },
];

// One dialog for everything personal: Account, Previews and AI. Each part is the same component
// that used to be its own dialog, shown inline. Account is its own section so more of it (name,
// password) can be added there without touching the other tabs.
export function SettingsDialog({
  admin,
  initialTab = "account",
  onClose,
  account,
}: {
  admin: boolean;
  initialTab?: SettingsTab;
  onClose: () => void;
  /** What the Account section needs when the person returns from a provider or a PIN link. */
  account: {
    notice?: string;
    reauthed?: boolean;
    pinToken?: string | null;
    onPinChanged?: (isSet: boolean) => void;
  };
}) {
  const [tab, setTab] = useState<SettingsTab>(initialTab);
  let content: ReactNode;
  if (tab === "account") {
    content = <AccountDialog inline onClose={onClose} {...account} />;
  } else if (tab === "previews") {
    content = <PreviewSettings inline onClose={onClose} />;
  } else {
    content = <AiSettings inline admin={admin} onClose={onClose} />;
  }
  return (
    <Modal title="Settings" onClose={onClose}>
      <div className="settings-tabs" role="tablist" aria-label="Settings">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`settings-tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls="settings-panel"
            className={tab === t.id ? "active" : undefined}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div
        id="settings-panel"
        role="tabpanel"
        aria-labelledby={`settings-tab-${tab}`}
      >
        {content}
      </div>
      <div className="actions">
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
    </Modal>
  );
}
