import { removeOverlayWithMotion } from "./overlayMotion.js";

let activeConfirmation = null;
let confirmationId = 0;

export function showConfirmDialog(message, {
  confirmLabel = "Delete",
  documentRef = document,
  windowRef = window,
} = {}) {
  activeConfirmation?.cancel();

  return new Promise((resolve) => {
    const previouslyFocused = documentRef.activeElement;
    const overlay = documentRef.createElement("div");
    overlay.className = "confirm-overlay";
    overlay.setAttribute("role", "alertdialog");
    overlay.setAttribute("aria-modal", "true");
    const messageElement = documentRef.createElement("p");
    messageElement.className = "confirm-message";
    confirmationId += 1;
    messageElement.id = `confirm-message-${confirmationId}`;
    messageElement.textContent = message;
    overlay.setAttribute("aria-describedby", messageElement.id);

    const actions = documentRef.createElement("div");
    actions.className = "confirm-actions";
    const cancelButton = documentRef.createElement("button");
    cancelButton.type = "button";
    cancelButton.className = "confirm-cancel";
    cancelButton.textContent = "Cancel";
    const confirmButton = documentRef.createElement("button");
    confirmButton.type = "button";
    confirmButton.className = "confirm-ok";
    confirmButton.textContent = confirmLabel;
    actions.append(cancelButton, confirmButton);

    const card = documentRef.createElement("div");
    card.className = "confirm-card";
    card.append(messageElement, actions);
    overlay.appendChild(card);

    let settled = false;
    const cleanup = () => {
      documentRef.removeEventListener("keydown", handleKeydown, true);
      if (activeConfirmation?.overlay === overlay) activeConfirmation = null;
    };
    const finish = (result) => {
      if (settled) return;
      settled = true;
      cleanup();
      void removeOverlayWithMotion(overlay, { windowRef }).then(() => {
        if (previouslyFocused?.isConnected) previouslyFocused.focus({ preventScroll: true });
        resolve(result);
      });
    };
    const handleKeydown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        finish(false);
        return;
      }
      if (event.key !== "Tab") return;
      const target = documentRef.activeElement;
      if (event.shiftKey && target === cancelButton) {
        event.preventDefault();
        confirmButton.focus();
      } else if (!event.shiftKey && target === confirmButton) {
        event.preventDefault();
        cancelButton.focus();
      }
    };

    cancelButton.addEventListener("click", () => finish(false));
    confirmButton.addEventListener("click", () => finish(true));
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) finish(false);
    });
    documentRef.addEventListener("keydown", handleKeydown, true);
    documentRef.body.appendChild(overlay);
    confirmButton.focus();
    activeConfirmation = { overlay, cancel: () => finish(false) };
  });
}
