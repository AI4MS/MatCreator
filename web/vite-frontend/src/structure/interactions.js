import * as THREE from "three";

function clearTransformSnapshot(state) {
  state.dragStartPositions = null;
  state.dragStartProxy = null;
  state.dragStartQuaternion = null;
  state.dragStartScale = null;
  state.dragRelativePositions = null;
}

function resetTransformForViewportDrag(state) {
  state.transform.detach();
  state.transform.axis = null;
  state.transform.dragging = false;
  state.controls.enabled = true;
  clearTransformSnapshot(state);
}

export function installStructureInteractions(state, container) {
  const raycaster = new THREE.Raycaster();
  const mouse = new THREE.Vector2();
  let down = null;
  let outsidePointerDown = false;
  let bondRebuildFrame = null;
  const scheduleBondRebuild = () => {
    if (bondRebuildFrame !== null) return;
    bondRebuildFrame = requestAnimationFrame(() => {
      bondRebuildFrame = null;
      state.bonds?.rebuild();
    });
  };

  const handleTransformDragging = (event) => {
    state.controls.enabled = !event.value;
  };
  const startTransform = () => {
    state.dragStartProxy = state.transformProxy?.position.clone();
    state.dragStartQuaternion = state.transformProxy?.quaternion.clone();
    state.dragStartScale = state.transformProxy?.scale.clone();
    state.dragStartPositions = new Map([...state.selected].map((id) => [id, state.meshes.get(id)?.position.clone()]));
    const inverse = state.dragStartQuaternion?.clone().invert();
    state.dragRelativePositions = new Map([...state.dragStartPositions].map(([id, position]) => [
      id,
      position?.clone().sub(state.dragStartProxy).applyQuaternion(inverse),
    ]));
  };
  const finishTransform = () => {
    if (state.dragStartProxy && state.selected.size) {
      const movedAtoms = [...state.selected]
        .map((id) => ({ id, xyz: state.meshes.get(id)?.position.toArray() }))
        .filter((atom) => atom.xyz);
      if (movedAtoms.some((atom) => {
        const start = state.dragStartPositions.get(atom.id);
        return start && atom.xyz.some((value, index) => Math.abs(value - start.getComponent(index)) > 1e-6);
      })) state.onMove?.(movedAtoms, { mode: state.transformMode });
    }
    clearTransformSnapshot(state);
  };
  const updateTransform = () => {
    if (!state.transform.dragging || !state.dragStartProxy || !state.dragStartPositions) return;
    const delta = state.transformProxy.position.clone().sub(state.dragStartProxy);
    for (const [id, start] of state.dragStartPositions) {
      const mesh = state.meshes.get(id);
      if (!mesh || !start) continue;
      if (state.transformMode === "rotate") {
        const relative = state.dragRelativePositions.get(id)?.clone();
        if (relative) mesh.position.copy(state.transformProxy.position).add(relative.applyQuaternion(state.transformProxy.quaternion));
      } else if (state.transformMode === "scale") {
        const relative = state.dragRelativePositions.get(id)?.clone();
        if (relative) {
          const ratio = state.transformProxy.scale.clone().divide(state.dragStartScale);
          relative.multiply(ratio).applyQuaternion(state.transformProxy.quaternion);
          mesh.position.copy(state.transformProxy.position).add(relative);
        }
      } else {
        mesh.position.copy(start).add(delta);
      }
    }
    scheduleBondRebuild();
  };

  state.transform.addEventListener("dragging-changed", handleTransformDragging);
  state.transform.addEventListener("mouseDown", startTransform);
  state.transform.addEventListener("mouseUp", finishTransform);
  state.transform.addEventListener("change", updateTransform);

  const rendererPointer = (event) => {
    const bounds = state.renderer.domElement.getBoundingClientRect();
    return {
      x: ((event.clientX - bounds.left) / bounds.width) * 2 - 1,
      y: -((event.clientY - bounds.top) / bounds.height) * 2 + 1,
      button: event.button,
    };
  };
  const pointerHitsTransformHandle = (event) => {
    if (!state.transformHelper || !state.selected.size) return false;
    // TransformControls ignores hover updates while `dragging` is true. Its
    // mouseUp event fires before that flag and the active axis are cleared, so
    // reactive scene updates can otherwise carry the previous axis into the
    // next gesture and turn an outside drag into another atom transform.
    state.transform.dragging = false;
    state.transform.axis = null;
    state.controls.enabled = true;
    clearTransformSnapshot(state);
    state.transform.pointerHover(rendererPointer(event));
    return state.transform.axis !== null;
  };
  const startBoxSelection = (event) => {
    if (event.button !== 0 || !event.shiftKey) return;
    const bounds = container.getBoundingClientRect();
    const start = [event.clientX - bounds.left, event.clientY - bounds.top];
    state.selectionBox = { start, additive: event.ctrlKey || event.metaKey };
    state.controls.enabled = false;
    const overlay = document.createElement("div");
    overlay.className = "structure-selection-box";
    overlay.style.cssText = `position:absolute;z-index:850;pointer-events:none;left:${start[0]}px;top:${start[1]}px;width:0;height:0;border:1px solid #60a5fa;background:rgba(96,165,250,.18);`;
    container.appendChild(overlay);
    state.selectionBox.element = overlay;
    event.preventDefault();
    event.stopPropagation();
  };
  const updateBoxSelection = (event) => {
    if (!state.selectionBox) return;
    const bounds = container.getBoundingClientRect();
    const [x0, y0] = state.selectionBox.start;
    const x = event.clientX - bounds.left;
    const y = event.clientY - bounds.top;
    Object.assign(state.selectionBox, { left: Math.min(x0, x), top: Math.min(y0, y), width: Math.abs(x - x0), height: Math.abs(y - y0) });
    Object.assign(state.selectionBox.element.style, { left: `${state.selectionBox.left}px`, top: `${state.selectionBox.top}px`, width: `${state.selectionBox.width}px`, height: `${state.selectionBox.height}px` });
  };
  const finishBoxSelection = () => {
    const box = state.selectionBox;
    if (!box) return;
    state.selectionBox = null;
    state.suppressClick = true;
    box.element.remove();
    state.controls.enabled = true;
    window.setTimeout(() => { state.suppressClick = false; }, 0);
    if (box.width < 5 && box.height < 5) return;
    const bounds = state.renderer.domElement.getBoundingClientRect();
    const selected = [];
    for (const [id, mesh] of state.meshes) {
      const point = mesh.getWorldPosition(new THREE.Vector3()).project(state.camera);
      const x = (point.x * .5 + .5) * bounds.width;
      const y = (-point.y * .5 + .5) * bounds.height;
      if (x >= box.left && x <= box.left + box.width && y >= box.top && y <= box.top + box.height) selected.push(id);
    }
    state.onBoxSelect?.(selected, box.additive);
  };
  const handlePointerDownCapture = (event) => {
    down = [event.clientX, event.clientY];
    outsidePointerDown = event.button === 0 && !event.shiftKey && state.selected.size > 0 && !pointerHitsTransformHandle(event);
    if (!outsidePointerDown) return;
    resetTransformForViewportDrag(state);
  };
  const finishPointerGesture = (event) => {
    finishBoxSelection();
    if (!outsidePointerDown || !down || Math.hypot(event.clientX - down[0], event.clientY - down[1]) <= 5) return;
    if (state.selected.size && state.transformProxy) state.transform.attach(state.transformProxy);
  };
  const handleClick = (event) => {
    if (state.suppressClick) return;
    if (outsidePointerDown) {
      outsidePointerDown = false;
      if (!down || Math.hypot(event.clientX - down[0], event.clientY - down[1]) <= 5) state.onSelect?.(null, false);
      return;
    }
    if (!down || Math.hypot(event.clientX - down[0], event.clientY - down[1]) > 5) return;
    const bounds = state.renderer.domElement.getBoundingClientRect();
    mouse.set(((event.clientX - bounds.left) / bounds.width) * 2 - 1, -((event.clientY - bounds.top) / bounds.height) * 2 + 1);
    raycaster.setFromCamera(mouse, state.camera);
    const hit = raycaster.intersectObjects([...state.meshes.values()])[0];
    state.onSelect?.(hit?.object?.userData?.atomId ?? null, event.ctrlKey || event.metaKey);
  };
  const preventContextMenu = (event) => event.preventDefault();

  state.renderer.domElement.addEventListener("pointerdown", handlePointerDownCapture, true);
  state.renderer.domElement.addEventListener("pointerdown", startBoxSelection);
  state.renderer.domElement.addEventListener("click", handleClick);
  state.renderer.domElement.addEventListener("contextmenu", preventContextMenu);
  window.addEventListener("pointermove", updateBoxSelection);
  window.addEventListener("pointerup", finishPointerGesture);

  return () => {
    if (bondRebuildFrame !== null) cancelAnimationFrame(bondRebuildFrame);
    state.transform.removeEventListener("dragging-changed", handleTransformDragging);
    state.transform.removeEventListener("mouseDown", startTransform);
    state.transform.removeEventListener("mouseUp", finishTransform);
    state.transform.removeEventListener("change", updateTransform);
    state.renderer?.domElement.removeEventListener("pointerdown", handlePointerDownCapture, true);
    state.renderer?.domElement.removeEventListener("pointerdown", startBoxSelection);
    state.renderer?.domElement.removeEventListener("click", handleClick);
    state.renderer?.domElement.removeEventListener("contextmenu", preventContextMenu);
    window.removeEventListener("pointermove", updateBoxSelection);
    window.removeEventListener("pointerup", finishPointerGesture);
  };
}
