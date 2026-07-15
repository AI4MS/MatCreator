import * as THREE from "three";
import { atomData, normalizeElement } from "./elements.js";
import { glossyFragment, glossyUniforms, glossyVertex } from "./shaders.js";

const BOND_RADIUS = 0.075;
const BOND_THRESHOLD_FACTOR = 0.6;
const MIN_BOND_LENGTH = 0.1;
const MAX_BONDED_ATOMS = 500;
const Y_AXIS = new THREE.Vector3(0, 1, 0);

function bondMaterial(element) {
  const [color] = atomData(normalizeElement(element));
  return new THREE.ShaderMaterial({
    vertexShader: glossyVertex,
    fragmentShader: glossyFragment,
    uniforms: glossyUniforms(new THREE.Color(color)),
    side: THREE.FrontSide,
    toneMapped: false,
  });
}

function positionHalfBond(mesh, start, end) {
  const direction = end.clone().sub(start);
  const length = direction.length();
  mesh.position.copy(start).add(end).multiplyScalar(0.5);
  mesh.scale.set(1, length, 1);
  mesh.quaternion.setFromUnitVectors(Y_AXIS, direction.normalize());
}

export function createBondRenderer(state) {
  let group = null;
  let geometry = null;
  const entries = new Map();

  const ensureResources = () => {
    if (group) return;
    group = new THREE.Group();
    group.userData.rendererObject = true;
    geometry = new THREE.CylinderGeometry(BOND_RADIUS, BOND_RADIUS, 1, 10);
    state.scene.add(group);
  };
  const removeEntry = (key) => {
    const entry = entries.get(key);
    if (!entry) return;
    entry.one.removeFromParent();
    entry.two.removeFromParent();
    entry.one.material.dispose();
    entry.two.material.dispose();
    entries.delete(key);
  };
  const reset = () => {
    for (const key of [...entries.keys()]) removeEntry(key);
    group?.removeFromParent();
    geometry?.dispose();
    group = null;
    geometry = null;
  };
  const rebuild = () => {
    if (!state.scene || state.atoms.length >= MAX_BONDED_ATOMS) {
      reset();
      return;
    }
    ensureResources();
    const active = new Set();
    for (let i = 0; i < state.atoms.length; i++) for (let j = i + 1; j < state.atoms.length; j++) {
      const atomOne = state.atoms[i];
      const atomTwo = state.atoms[j];
      const pointOne = state.meshes.get(atomOne.id)?.position;
      const pointTwo = state.meshes.get(atomTwo.id)?.position;
      if (!pointOne || !pointTwo) continue;
      const distance = pointOne.distanceTo(pointTwo);
      const threshold = (atomData(normalizeElement(atomOne.element))[2] + atomData(normalizeElement(atomTwo.element))[2]) * BOND_THRESHOLD_FACTOR;
      if (distance > threshold || distance < MIN_BOND_LENGTH) continue;
      const key = `${i}:${j}`;
      active.add(key);
      let entry = entries.get(key);
      if (!entry) {
        entry = {
          one: new THREE.Mesh(geometry, bondMaterial(atomOne.element)),
          two: new THREE.Mesh(geometry, bondMaterial(atomTwo.element)),
        };
        entry.one.userData = { atomIds: [atomOne.id, atomTwo.id] };
        entry.two.userData = { atomIds: [atomOne.id, atomTwo.id] };
        group.add(entry.one, entry.two);
        entries.set(key, entry);
      }
      const midpoint = pointOne.clone().add(pointTwo).multiplyScalar(0.5);
      positionHalfBond(entry.one, pointOne, midpoint);
      positionHalfBond(entry.two, midpoint, pointTwo);
    }
    for (const key of [...entries.keys()]) if (!active.has(key)) removeEntry(key);
  };

  return { rebuild, reset, dispose: reset };
}
