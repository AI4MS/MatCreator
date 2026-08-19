const STRUCTURE_EXTENSIONS = new Set([".cif", ".xyz", ".extxyz", ".vasp"]);
const IMAGE_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".gif", ".svg"]);

function isVaspStructureFilename(name) {
  // VASP workflows often generate variants such as POSCAR_water_layer2 or
  // POSCAR-2_water6. These have no informative extension, but are still POSCARs.
  return /^(?:poscar|contcar)(?:[_.-].*)?$/i.test(name);
}

export function classifyPath(path) {
  const name = path.split("/").pop();
  const dotIndex = name.lastIndexOf(".");
  const extension = dotIndex >= 0 ? name.slice(dotIndex).toLowerCase() : "";
  if (STRUCTURE_EXTENSIONS.has(extension) || isVaspStructureFilename(name)) return "structure";
  if (IMAGE_EXTENSIONS.has(extension)) return "image";
  return "artifact";
}

function formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function createTreeIcon(type) {
  const icon = document.createElement("span");
  icon.className = `tree-icon tree-icon-${type}`;
  icon.setAttribute("aria-hidden", "true");

  const paths = {
    download: '<path d="M12 3v11m0 0 4-4m-4 4-4-4M5 21h14" />',
    eye: '<path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z" /><circle cx="12" cy="12" r="2.5" />',
    cube: '<path d="m12 3 7 4v10l-7 4-7-4V7l7-4Z" /><path d="m5 7 7 4 7-4M12 11v10" />',
    file: '<path d="M6 3h7l5 5v13H6z" /><path d="M13 3v5h5M9 13h6M9 17h4" />',
    folder: '<path d="M3 6.5h6l2 2H21v9.5A2 2 0 0 1 19 20H5a2 2 0 0 1-2-2z" /><path d="M3 6.5A2.5 2.5 0 0 1 5.5 4H9l2 2.5" />',
  };
  icon.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${paths[type]}</svg>`;
  return icon;
}

export function createSessionFileTree({ getSessionId, pathToApiUrl, openStructure, openFile }) {
  function createFileItem(file) {
    const item = document.createElement("li");
    item.className = "tree-file";
    const type = classifyPath(file.path);
    const open = type === "structure"
      ? () => openStructure({
        path: file.path,
        name: file.relname,
        url: pathToApiUrl(file.path),
      })
      : () => openFile({ path: file.path, name: file.relname });

    item.addEventListener("dblclick", (event) => {
      // The controls retain their own behavior; a double-click on the row opens
      // the file using the same viewer as the View action.
      if (event.target.closest("a, button")) return;
      open();
    });

    const indent = document.createElement("span");
    indent.className = "tree-node-indent";
    indent.setAttribute("aria-hidden", "true");
    item.appendChild(indent);

    item.appendChild(createTreeIcon(type === "structure" ? "cube" : "file"));

    const name = document.createElement("span");
    name.className = "tree-filename";
    name.textContent = file.relname;
    item.appendChild(name);

    const size = document.createElement("span");
    size.className = "tree-filesize";
    size.textContent = formatFileSize(file.size);
    item.appendChild(size);

    const actions = document.createElement("div");
    actions.className = "tree-actions";

    const download = document.createElement("a");
    download.href = `/api/workspace/files?path=${encodeURIComponent(file.path)}`;
    download.download = file.relname;
    download.className = "tree-btn tree-btn-download";
    download.title = "Download";
    download.setAttribute("aria-label", "Download");
    download.appendChild(createTreeIcon("download"));
    actions.appendChild(download);

    const view = document.createElement("button");
    view.type = "button";
    view.className = "tree-btn tree-btn-view";
    if (type === "structure") {
      view.title = "View 3D";
      view.setAttribute("aria-label", "View 3D");
      view.appendChild(createTreeIcon("cube"));
    } else {
      view.title = "View";
      view.setAttribute("aria-label", "View");
      view.appendChild(createTreeIcon("eye"));
    }
    view.addEventListener("click", open);
    actions.appendChild(view);
    item.appendChild(actions);
    return item;
  }

  function buildTree(files, prefix) {
    const root = { children: {}, files: [] };
    for (const file of files) {
      const relativePath = file.path.slice(prefix.length).replace(/^\//, "");
      const parts = relativePath.split("/");
      const directories = parts.slice(0, -1);
      let node = root;
      for (const directory of directories) {
        if (!node.children[directory]) {
          node.children[directory] = { name: directory, children: {}, files: [] };
        }
        node = node.children[directory];
      }
      node.files.push({ ...file, relname: parts.at(-1), relpath: relativePath });
    }
    return root;
  }

  function renderNode(node, container) {
    for (const directoryName of Object.keys(node.children).sort()) {
      const item = document.createElement("li");
      item.className = "tree-dir-node";
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.className = "tree-dir-summary";
      summary.append(createTreeIcon("folder"));
      const label = document.createElement("span");
      label.textContent = `${directoryName}/`;
      summary.appendChild(label);
      details.appendChild(summary);
      const children = document.createElement("ul");
      children.className = "tree-dir-children";
      renderNode(node.children[directoryName], children);
      details.appendChild(children);
      item.appendChild(details);
      container.appendChild(item);
    }

    const files = node.files.slice().sort((left, right) => left.relname.localeCompare(right.relname));
    for (const file of files) container.appendChild(createFileItem(file));
  }

  function commonPathPrefix(files) {
    const sessionId = getSessionId();
    const sessionIndex = files[0].path.indexOf(sessionId);
    if (sessionIndex >= 0) return files[0].path.slice(0, sessionIndex + sessionId.length);

    let common = files[0].path;
    for (const file of files) {
      let index = 0;
      while (index < common.length && index < file.path.length && common[index] === file.path[index]) index++;
      common = common.slice(0, index);
    }
    return common.slice(0, common.lastIndexOf("/") + 1);
  }

  function render(files) {
    const rootElement = document.getElementById("session-files-tree");
    rootElement.innerHTML = "";
    if (!files.length) {
      const empty = document.createElement("li");
      empty.className = "empty";
      empty.textContent = "No files yet";
      rootElement.appendChild(empty);
      return;
    }

    renderNode(buildTree(files, commonPathPrefix(files)), rootElement);
  }

  return { render };
}
