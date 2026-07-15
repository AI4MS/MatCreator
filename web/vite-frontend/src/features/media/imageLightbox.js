export function createImageLightbox() {
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

    open(src) {
      this.scale = 1;
      this.translateX = 0;
      this.translateY = 0;
      this.img.src = src;
      this.img.style.transform = "";
      this.el.classList.remove("hidden");
      this.updateLabel();
    },

    close() {
      this.el.classList.add("hidden");
      this.img.src = "";
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
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !lightbox.el.classList.contains("hidden")) lightbox.close();
  });

  return lightbox;
}