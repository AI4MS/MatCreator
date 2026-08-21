import { createDialogController } from "../../shared/ui/dialog.js";

export function createImageLightbox() {
  let dialog;
  const lightbox = {
    el: document.getElementById("image-lightbox"),
    img: document.getElementById("lightbox-img"),
    viewport: document.getElementById("lightbox-viewport"),
    label: document.getElementById("lightbox-zoom-label"),
    scale: 1,
    translateX: 0,
    translateY: 0,
    dragging: false,
    dragStartX: 0,
    dragStartY: 0,
    dragStartTranslateX: 0,
    dragStartTranslateY: 0,
    closeTimer: null,
    closeTransitionHandler: null,

    open(src) {
      window.clearTimeout(this.closeTimer);
      if (this.closeTransitionHandler) {
        this.el.removeEventListener("transitionend", this.closeTransitionHandler);
        this.closeTransitionHandler = null;
      }
      this.scale = 1;
      this.translateX = 0;
      this.translateY = 0;
      this.img.src = src;
      this.img.style.transform = "";
      this.updateLabel();
      dialog.open();
    },

    close() {
      dialog.close();
    },

    applyTransform() {
      this.img.style.transform = `translate(${this.translateX}px, ${this.translateY}px) scale(${this.scale})`;
      this.updateLabel();
    },

    updateLabel() {
      if (this.label) this.label.textContent = `${Math.round(this.scale * 100)}%`;
    },

    zoomIn() {
      this.scale = Math.min(this.scale * 1.3, 20);
      this.applyTransform();
    },

    zoomOut() {
      const newScale = this.scale / 1.3;
      if (newScale < 0.1) return;
      const factor = newScale / this.scale;
      this.scale = newScale;
      this.translateX *= factor;
      this.translateY *= factor;
      this.applyTransform();
    },

    resetZoom() {
      this.scale = 1;
      this.translateX = 0;
      this.translateY = 0;
      this.applyTransform();
    },
  };

  dialog = createDialogController({
    element: lightbox.el,
    label: "Image preview",
    initialFocus: "#lightbox-close",
    onClose: () => {
      window.clearTimeout(lightbox.closeTimer);
      if (lightbox.closeTransitionHandler) {
        lightbox.el.removeEventListener("transitionend", lightbox.closeTransitionHandler);
        lightbox.closeTransitionHandler = null;
      }
      if (window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches) {
        lightbox.img.removeAttribute("src");
        return;
      }

      const releaseImage = () => {
        window.clearTimeout(lightbox.closeTimer);
        if (lightbox.closeTransitionHandler) {
          lightbox.el.removeEventListener("transitionend", lightbox.closeTransitionHandler);
          lightbox.closeTransitionHandler = null;
        }
        if (!dialog.isOpen()) lightbox.img.removeAttribute("src");
      };
      lightbox.closeTransitionHandler = (event) => {
        if (event.target === lightbox.el && event.propertyName === "opacity") releaseImage();
      };
      lightbox.el.addEventListener("transitionend", lightbox.closeTransitionHandler);
      lightbox.closeTimer = window.setTimeout(releaseImage, 250);
    },
  });

  lightbox.viewport?.addEventListener("wheel", (event) => {
    event.preventDefault();
    if (event.deltaY < 0) lightbox.zoomIn();
    else lightbox.zoomOut();
  }, { passive: false });

  lightbox.viewport?.addEventListener("mousedown", (event) => {
    if (event.target !== lightbox.img) return;
    lightbox.dragging = true;
    lightbox.dragStartX = event.clientX;
    lightbox.dragStartY = event.clientY;
    lightbox.dragStartTranslateX = lightbox.translateX;
    lightbox.dragStartTranslateY = lightbox.translateY;
    event.preventDefault();
  });

  document.addEventListener("mousemove", (event) => {
    if (!lightbox.dragging) return;
    lightbox.translateX = lightbox.dragStartTranslateX + event.clientX - lightbox.dragStartX;
    lightbox.translateY = lightbox.dragStartTranslateY + event.clientY - lightbox.dragStartY;
    lightbox.applyTransform();
  });

  document.addEventListener("mouseup", () => {
    lightbox.dragging = false;
  });

  lightbox.viewport?.addEventListener("click", (event) => {
    if (event.target === lightbox.viewport) lightbox.close();
  });

  document.getElementById("lightbox-close")?.addEventListener("click", () => lightbox.close());
  document.getElementById("lightbox-zoom-in")?.addEventListener("click", () => lightbox.zoomIn());
  document.getElementById("lightbox-zoom-out")?.addEventListener("click", () => lightbox.zoomOut());
  document.getElementById("lightbox-zoom-reset")?.addEventListener("click", () => lightbox.resetZoom());
  return lightbox;
}
