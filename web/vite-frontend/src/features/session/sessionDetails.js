import { createDialogController } from "../../shared/ui/dialog.js";

const DATE_FORMATTER = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

function formatSessionDate(value) {
  if (value === null || value === undefined || value === "") return "—";
  const numericValue = typeof value === "number"
    ? value
    : typeof value === "string" && /^\d+(\.\d+)?$/.test(value.trim())
      ? Number(value)
      : NaN;
  const timestamp = Number.isFinite(numericValue)
    ? (Math.abs(numericValue) < 1e12 ? numericValue * 1000 : numericValue)
    : value;
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? "—" : DATE_FORMATTER.format(date);
}

function text(value, fallback = "—") {
  const normalized = String(value ?? "").trim();
  return normalized || fallback;
}

/** Creates the metadata dialog opened from a session item's context menu. */
export function createSessionDetailsController({ documentRef = document, getStatus } = {}) {
  let overlay;
  let dialog;
  let closeButton;

  function ensureDialog() {
    if (dialog) return;

    overlay = documentRef.createElement("div");
    overlay.className = "session-details-overlay hidden";
    overlay.setAttribute("aria-hidden", "true");

    const card = documentRef.createElement("section");
    card.className = "session-details-card";

    const header = documentRef.createElement("div");
    header.className = "session-details-header";
    const title = documentRef.createElement("h2");
    title.className = "session-details-title";
    title.id = "session-details-title";
    title.textContent = "Session details";
    closeButton = documentRef.createElement("button");
    closeButton.type = "button";
    closeButton.className = "ghost session-details-close";
    closeButton.textContent = "✕";
    closeButton.setAttribute("aria-label", "Close session details");
    header.append(title, closeButton);

    const hint = documentRef.createElement("p");
    hint.className = "session-details-hint";
    hint.textContent = "Technical metadata for debugging.";
    const fields = documentRef.createElement("dl");
    fields.className = "session-details-fields";
    card.append(header, hint, fields);
    overlay.appendChild(card);
    documentRef.body.appendChild(overlay);

    dialog = createDialogController({
      element: overlay,
      initialFocus: () => closeButton,
      labelledBy: title.id,
    });
    closeButton.addEventListener("click", () => dialog.close("button"));
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) dialog.close("backdrop");
    });
  }

  function setField(fields, label, value, { code = false } = {}) {
    const row = documentRef.createElement("div");
    row.className = "session-details-field";
    const term = documentRef.createElement("dt");
    term.textContent = label;
    const definition = documentRef.createElement("dd");
    if (code) {
      const codeElement = documentRef.createElement("code");
      codeElement.textContent = value;
      definition.appendChild(codeElement);
    } else {
      definition.textContent = value;
    }
    row.append(term, definition);
    fields.appendChild(row);
  }

  function open(session, owner) {
    if (!session) return;
    ensureDialog();
    const fields = overlay.querySelector(".session-details-fields");
    fields.replaceChildren();
    setField(fields, "Name", text(session.summary, "Unnamed session"));
    setField(fields, "Session ID", text(session.id), { code: true });
    setField(fields, "Created", formatSessionDate(session.createTime));
    setField(fields, "Last used", formatSessionDate(session.lastUpdateTime));
    setField(fields, "Status", text(getStatus?.(session, owner) || session.status || session.phase, "Idle"));
    setField(fields, "Owner", text(session.userId || owner));
    setField(fields, "App", text(session.appName));
    dialog.open();
  }

  return { open, close: () => dialog?.close("programmatic") };
}

export { formatSessionDate };
