export function removeOverlayWithMotion(overlay, { windowRef = window } = {}) {
  if (!overlay?.isConnected) return Promise.resolve();
  if (windowRef.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches) {
    overlay.remove();
    return Promise.resolve();
  }
  if (overlay.classList.contains("is-closing")) {
    return overlay._motionRemoval || Promise.resolve();
  }

  overlay.classList.add("is-closing");
  overlay._motionRemoval = new Promise((resolve) => {
    let fallbackTimer;
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      windowRef.clearTimeout(fallbackTimer);
      overlay.removeEventListener("animationend", onAnimationEnd);
      overlay.remove();
      resolve();
    };
    const onAnimationEnd = (event) => {
      if (event.target === overlay) finish();
    };
    overlay.addEventListener("animationend", onAnimationEnd);
    fallbackTimer = windowRef.setTimeout(finish, 250);
  });
  return overlay._motionRemoval;
}
