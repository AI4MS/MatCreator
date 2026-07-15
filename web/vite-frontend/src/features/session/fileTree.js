const STRUCTURE_EXTENSIONS = new Set([".cif", ".xyz", ".extxyz", ".vasp"]);
const STRUCTURE_NAMES = new Set(["poscar", "contcar"]);
const IMAGE_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".gif", ".svg"]);

export function classifyPath(path) {
  const name = path.split("/").pop();
  const dotIndex = name.lastIndexOf(".");
  const extension = dotIndex >= 0 ? name.slice(dotIndex).toLowerCase() : "";
  if (STRUCTURE_EXTENSIONS.has(extension) || STRUCTURE_NAMES.has(name.toLowerCase())) return "structure";
  if (IMAGE_EXTENSIONS.has(extension)) return "image";
  return "artifact";
}

function formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function createSessionFileTree({ getSessionId, pathToApiUrl, openStructure, openFile }) {
  function createFileItem(file) {
    const item = document.createElement("li");
    item.className = "tree-file";

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
    download.className = "tree-btn";
    download.title = "Download";
    download.textContent = "↓";
    actions.appendChild(download);

    const view = document.createElement("button");
    view.className = "tree-btn";
    if (classifyPath(file.path) === "structure") {
      view.title = "View 3D";
      view.textContent = "⬡";
      view.addEventListener("click", () => openStructure({
        path: file.path,
        name: file.relname,
        url: pathToApiUrl(file.path),
      }));
    } else {
      view.title = "View";
      view.textContent = "👁";
      view.addEventListener("click", () => openFile({ path: file.path, name: file.relname }));
    }
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
      summary.textContent = `${directoryName}/`;
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