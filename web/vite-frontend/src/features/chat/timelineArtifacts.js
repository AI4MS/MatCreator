import { classifyPath } from "../session/fileTree.js";

/** Creates DOM renderers for files referenced by chat timeline entries. */
export function createTimelineArtifactRenderer({
  pathToApiUrl,
  openStructure,
  openLightbox,
  updatePreservingReadingPosition,
}) {
  function createStructureViewButton(path) {
    const btn = document.createElement("button");
    btn.className = "ghost structure-view-btn";
    btn.type = "button";
    btn.title = path;
    const filename = path.split("/").pop();
    btn.setAttribute("aria-label", `View structure ${filename}`);

    const icon = document.createElement("span");
    icon.className = "structure-view-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.innerHTML = `
      <svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="m16 4 9 5.2v10.6L16 25l-9-5.2V9.2L16 4Z" />
        <path d="m7 9.2 9 5.3 9-5.3M16 14.5V25" />
        <circle cx="16" cy="14.5" r="2.2" /><circle cx="7" cy="9.2" r="1.5" />
        <circle cx="25" cy="9.2" r="1.5" /><circle cx="16" cy="25" r="1.5" />
      </svg>`;
    const label = document.createElement("span");
    label.className = "structure-view-label";
    label.textContent = filename;
    btn.append(icon, label);
    btn.addEventListener("click", () => openStructure({ path, url: pathToApiUrl(path) }));
    return btn;
  }

  function createStructureViewButtonGroup(paths) {
    const group = document.createElement("div");
    group.className = "structure-view-button-group";
    paths.forEach((path) => group.appendChild(createStructureViewButton(path)));
    return group;
  }

  function createArtifactListItem(path) {
    const li = document.createElement("li");
    li.title = path;
    if (classifyPath(path) === "structure") li.appendChild(createStructureViewButtonGroup([path]));
    else li.textContent = path.split("/").pop();
    return li;
  }

  function createImageLoadFallback(path) {
    const fallback = document.createElement("div");
    fallback.className = "timeline-image-error";
    fallback.setAttribute("role", "alert");
    fallback.textContent = `⚠ Image preview unavailable: ${path.split("/").pop()}`;
    return fallback;
  }

  function createTimelineImage(path) {
    const wrap = document.createElement("div");
    wrap.className = "timeline-image-wrap";
    const loading = document.createElement("div");
    loading.className = "timeline-image-loading";
    loading.textContent = `Loading image: ${path.split("/").pop()}`;
    const img = document.createElement("img");
    img.className = "timeline-image";
    img.alt = path.split("/").pop();
    img.hidden = true;
    img.style.cursor = "zoom-in";
    img.addEventListener("load", () => updatePreservingReadingPosition(() => {
      loading.remove();
      img.hidden = false;
    }));
    img.addEventListener("error", () => updatePreservingReadingPosition(() => {
      img.remove();
      loading.replaceWith(createImageLoadFallback(path));
    }), { once: true });
    img.addEventListener("click", () => openLightbox(img.src));
    img.src = pathToApiUrl(path);
    wrap.append(loading, img);
    return wrap;
  }

  return { createArtifactListItem, createStructureViewButtonGroup, createTimelineImage };
}
