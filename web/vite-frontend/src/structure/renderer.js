import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { TransformControls } from "three/examples/jsm/controls/TransformControls.js";
import { createBondRenderer } from "./bonds.js";
import { atomData, normalizeElement } from "./elements.js";
import { installStructureInteractions } from "./interactions.js";
import { glossyFragment, glossyUniforms, glossyVertex } from "./shaders.js";

const vec = (value) => new THREE.Vector3(Number(value[0]), Number(value[1]), Number(value[2]));

export { atomData, normalizeElement };

function material(color) {
  return new THREE.ShaderMaterial({
    vertexShader: glossyVertex,
    fragmentShader: glossyFragment,
    uniforms: glossyUniforms(color),
    side: THREE.FrontSide,
    toneMapped: false,
  });
}

export function createStructureRenderer() {
  const state = { scene: null, camera: null, renderer: null, controls: null, transform: null, transformHelper: null, transformProxy: null, transformMode: "translate", dragStartPositions: null, dragStartProxy: null, dragStartQuaternion: null, dragStartScale: null, dragRelativePositions: null, meshes: new Map(), atoms: [], lattice: null, selected: new Set(), theme: "dark", hasSynced: false, onSelect: null, onMove: null, onBoxSelect: null, resizeObserver: null, frame: 0, gizmo: null, gizmoAxes: [], selectionBox: null, suppressClick: false, bonds: null };
  state.bonds = createBondRenderer(state);
  const api = {
    init(container, { background = "#06080f", onSelect, onMove, onBoxSelect } = {}) {
      const rect = container.getBoundingClientRect();
      state.scene = new THREE.Scene(); state.scene.background = new THREE.Color(background); state.theme = background === "#f8fbff" ? "light" : "dark";
      state.scene.add(new THREE.HemisphereLight(0xffffff, 0x25324a, 2));
      const bondLight = new THREE.DirectionalLight(0xffffff, 2); bondLight.position.set(8, 10, 12); state.scene.add(bondLight);
      state.camera = new THREE.PerspectiveCamera(42, Math.max(1, rect.width) / Math.max(1, rect.height), 0.05, 2000);
      state.camera.up.set(0, 0, 1); state.camera.position.set(18, 20, 14);
      state.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
      state.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2)); state.renderer.setSize(Math.max(1, rect.width), Math.max(1, rect.height));
      state.renderer.domElement.style.cssText = "display:block;width:100%;height:100%;touch-action:none"; container.appendChild(state.renderer.domElement);
      state.controls = new OrbitControls(state.camera, state.renderer.domElement); state.controls.enableDamping = true; state.controls.target.set(0, 0, 0);
      state.transform = new TransformControls(state.camera, state.renderer.domElement); state.transform.setMode("translate");
      // Modern Three.js exposes TransformControls' renderable as a helper;
      // the controls object itself is no longer an Object3D.
      state.transformHelper = state.transform.getHelper?.();
      if (state.transformHelper) state.scene.add(state.transformHelper);
      // TransformControls manipulates one Object3D. A pivot lets the same
      // gizmo translate a whole selection while the selected meshes follow it.
      state.transformProxy = new THREE.Object3D();
      state.scene.add(state.transformProxy);
      state.onSelect = onSelect; state.onMove = onMove; state.onBoxSelect = onBoxSelect;
      const disposeInteractions = installStructureInteractions(state, container);
      state.resizeObserver = new ResizeObserver(() => api.resize()); state.resizeObserver.observe(container);
      const gizmo = document.createElement("canvas"); gizmo.width = gizmo.height = 200; gizmo.title = "Click a lattice axis to align the camera"; gizmo.style.cssText = "position:absolute;right:12px;bottom:12px;width:100px;height:100px;z-index:840;pointer-events:auto;cursor:pointer"; container.appendChild(gizmo); state.gizmo = gizmo;
      const drawGizmo = () => {
        const ctx = gizmo.getContext("2d"), size = 100, center = size / 2, radius = 30; const lightMode = state.theme === "light"; ctx.clearRect(0, 0, 200, 200); ctx.save(); ctx.scale(2, 2); ctx.beginPath(); ctx.arc(center, center, 45, 0, Math.PI * 2); ctx.fillStyle = lightMode ? "rgba(255,255,255,.82)" : "rgba(15,23,42,.64)"; ctx.fill(); ctx.strokeStyle = lightMode ? "rgba(51,65,85,.35)" : "rgba(148,163,184,.35)"; ctx.stroke();
        const vectors = state.lattice?.length === 3 ? state.lattice.map(vec) : [new THREE.Vector3(1,0,0), new THREE.Vector3(0,1,0), new THREE.Vector3(0,0,1)];
        const labels = ["a", "b", "c"], colors = ["#ef4444", "#22c55e", "#3b82f6"], view = new THREE.Matrix3().setFromMatrix4(state.camera.matrixWorldInverse);
        state.gizmoAxes = vectors.map((axis, index) => ({ index, world: axis.normalize(), axis: axis.normalize().applyMatrix3(view) }));
        state.gizmoAxes.slice().sort((left, right) => left.axis.z - right.axis.z).forEach(({ index, axis }) => { const endX = center + axis.x * radius, endY = center - axis.y * radius; ctx.globalAlpha = axis.z > 0 ? 1 : .4; ctx.strokeStyle = colors[index]; ctx.fillStyle = colors[index]; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(center, center); ctx.lineTo(endX, endY); ctx.stroke(); ctx.beginPath(); ctx.arc(endX, endY, 3, 0, Math.PI * 2); ctx.fill(); ctx.globalAlpha = 1; ctx.fillStyle = lightMode ? "#132033" : "#e2e8f0"; ctx.font = "12px sans-serif"; ctx.fillText(labels[index], endX + 6, endY + 4); }); ctx.restore();
      };
      const alignCameraToGizmo = (event) => { const rectNow = gizmo.getBoundingClientRect(), x = (event.clientX - rectNow.left) * 100 / rectNow.width, y = (event.clientY - rectNow.top) * 100 / rectNow.height; const axis = state.gizmoAxes.map((item) => ({ ...item, distance: Math.hypot(x - (50 + item.axis.x * 30), y - (50 - item.axis.y * 30)) })).sort((left, right) => left.distance - right.distance)[0]; if (!axis || axis.distance > 18) return; const distance = state.camera.position.distanceTo(state.controls.target); state.camera.position.copy(state.controls.target).add(axis.world.multiplyScalar(event.shiftKey ? -distance : distance)); state.camera.lookAt(state.controls.target); state.controls.update(); };
      gizmo.addEventListener("click", alignCameraToGizmo);
      state.cleanupEvents = () => { disposeInteractions(); gizmo.removeEventListener("click", alignCameraToGizmo); };
      const loop = () => { state.frame = requestAnimationFrame(loop); state.controls.update(); drawGizmo(); state.renderer.render(state.scene, state.camera); }; loop();
      return api;
    },
    sync({ atoms = [], lattice = null, selected = [] } = {}) {
      if (!state.scene) return; state.atoms = atoms; state.lattice = lattice; state.selected = new Set(selected);
      state.bonds.reset();
      for (const object of [...state.scene.children]) if (object.userData?.rendererObject) { state.scene.remove(object); object.geometry?.dispose(); object.material?.dispose(); }
      state.meshes.clear(); state.transform.detach(); state.transformProxy?.position.set(0, 0, 0);
      const points = atoms.map((atom) => vec([atom.x, atom.y, atom.z]));
      const center = points.reduce((sum, point) => sum.add(point), new THREE.Vector3()); if (points.length) center.multiplyScalar(1 / points.length);
      if (!state.hasSynced) {
        state.controls.target.copy(center);
        const spread = Math.max(3, ...points.map((point) => point.distanceTo(center)));
        state.camera.position.copy(center).add(new THREE.Vector3(1.15, 1.25, .9).normalize().multiplyScalar(spread * 3.4)); state.camera.lookAt(center);
        state.hasSynced = true;
      }
      const geometry = new THREE.SphereGeometry(1, atoms.length > 1000 ? 12 : 24, atoms.length > 1000 ? 12 : 24);
      for (const atom of atoms) {
        const symbol = normalizeElement(atom.element), [hex, covalent] = atomData(symbol); const mesh = new THREE.Mesh(geometry, material(new THREE.Color(hex)));
        mesh.position.set(atom.x, atom.y, atom.z); mesh.scale.setScalar(covalent * 0.42); mesh.userData = { rendererObject: true, atomId: atom.id }; mesh.material.uniforms.uSelectionFactor.value = state.selected.has(atom.id) ? 1 : 0; state.scene.add(mesh); state.meshes.set(atom.id, mesh);
      }
      if (lattice?.length === 3) {
        const [a, b, c] = lattice.map(vec); const origin = new THREE.Vector3(); const corners = [[origin,a],[origin,b],[origin,c],[a,a.clone().add(b)],[a,a.clone().add(c)],[b,a.clone().add(b)],[b,b.clone().add(c)],[c,a.clone().add(c)],[c,b.clone().add(c)],[a.clone().add(b),a.clone().add(b).add(c)],[a.clone().add(c),a.clone().add(c).add(b)],[b.clone().add(c),b.clone().add(c).add(a)]]; const positions = corners.flatMap(([p, q]) => [p.x,p.y,p.z,q.x,q.y,q.z]); const line = new THREE.LineSegments(new THREE.BufferGeometry().setAttribute("position", new THREE.Float32BufferAttribute(positions, 3)), new THREE.LineBasicMaterial({ color: 0x71809c, transparent: true, opacity: 0.6 })); line.userData.rendererObject = true; state.scene.add(line);
      }
      state.bonds.rebuild();
    },
    select(ids = []) {
      state.selected = new Set(ids);
      for (const [id, mesh] of state.meshes) mesh.material.uniforms.uSelectionFactor.value = state.selected.has(id) ? 1 : 0;
      state.transform.detach();
      if (!state.selected.size || !state.transformProxy) return;
      const positions = [...state.selected].map((id) => state.meshes.get(id)?.position).filter(Boolean);
      if (!positions.length) return;
      state.transformProxy.position.copy(positions.reduce((sum, position) => sum.add(position), new THREE.Vector3()).multiplyScalar(1 / positions.length));
      state.transformProxy.quaternion.identity();
      state.transformProxy.scale.set(1, 1, 1);
      state.transform.attach(state.transformProxy);
    },
    setTransformMode(mode = "translate") {
      state.transformMode = ["translate", "rotate", "scale"].includes(mode) ? mode : "translate";
      state.transform?.setMode(state.transformMode);
      state.transform?.setSpace(state.transformMode === "translate" ? "world" : "local");
    },
    setTheme(theme) { state.theme = theme === "light" ? "light" : "dark"; if (state.scene) state.scene.background.set(state.theme === "light" ? "#f8fbff" : "#06080f"); },
    resize() { if (!state.renderer) return; const rect = state.renderer.domElement.parentElement.getBoundingClientRect(); state.camera.aspect = Math.max(1, rect.width) / Math.max(1, rect.height); state.camera.updateProjectionMatrix(); state.renderer.setSize(Math.max(1, rect.width), Math.max(1, rect.height), false); },
    dispose() { cancelAnimationFrame(state.frame); state.cleanupEvents?.(); state.selectionBox?.element.remove(); state.gizmo?.remove(); state.resizeObserver?.disconnect(); state.bonds.dispose(); state.transformHelper?.removeFromParent(); state.transformProxy?.removeFromParent(); state.transform?.dispose(); state.controls?.dispose(); state.renderer?.dispose(); state.renderer?.domElement.remove(); }
  }; return api;
}
