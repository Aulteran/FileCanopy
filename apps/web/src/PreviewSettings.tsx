import { useEffect, useState } from "react";
import { ApiError, api } from "./api";
import { Modal } from "./Dialogs";
import { describeError } from "./format";

// Per-person switch for server-side previews of Private and Confidential files (M10). Off by
// default. On means this server may read such files to make previews, text and thumbnails; it
// never means a cloud service gets them.
export function PreviewSettings({ onClose }: { onClose: () => void }) {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [converter, setConverter] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    api
      .localProcessing()
      .then((s) => {
        if (!live) return;
        setEnabled(s.enabled);
        setConverter(s.converter);
      })
      .catch(() => live && setError(describeError("UNKNOWN")));
    return () => {
      live = false;
    };
  }, []);

  async function change(next: boolean): Promise<void> {
    try {
      const s = await api.setLocalProcessing(next);
      setEnabled(s.enabled);
      setError("");
    } catch (err) {
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
    }
  }

  return (
    <Modal title="Preview settings" onClose={onClose}>
      <label>
        <input
          type="checkbox"
          checked={enabled === true}
          disabled={enabled === null}
          onChange={(e) => void change(e.target.checked)}
        />
        Let this server make previews of my Private and Confidential files
      </label>
      <p className="muted">
        Previews, text extraction and thumbnails need the server to read the
        file. That happens on your own server and nothing is sent to a cloud
        service. Confidential files still need to be unlocked first. Turning
        this off makes earlier previews unreachable straight away.
      </p>
      <p className="muted">
        {converter
          ? "The Word, Excel and PowerPoint converter is running."
          : "The Word, Excel and PowerPoint converter is not running; those files offer a download instead."}
      </p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="actions">
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
    </Modal>
  );
}
