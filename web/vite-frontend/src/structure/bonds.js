import * as THREE from "three";
import { atomData, normalizeElement } from "./elements.js";
import { glossyFragment, glossyUniforms, glossyVertex } from "./shaders.js";

const BOND_RADIUS = 0.075;
const BOND_THRESHOLD_FACTOR = 0.6;
const MIN_BOND_LENGTH = 0.1;
const MAX_BONDED_ATOMS = 500;
const Y_AXIS = new THREE.Vector3(0, 1, 0);
const IMAGE_OFFSETS = [-1, 0, 1];

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
  const fractionalMatrix = () => {
    const lattice = state.lattice;
    if (!lattice || lattice.length !== 3 || lattice.some((vector) => vector.length !== 3)) return null;
    return new THREE.Matrix3().set(
      lattice[0][0], lattice[1][0], lattice[2][0],
      lattice[0][1], lattice[1][1], lattice[2][1],
      lattice[0][2], lattice[1][2], lattice[2][2],
    ).invert();
  };
  const halfBondEnd = (start, end, inverseLattice) => {
    const direction = end.clone().sub(start);
    let fraction = 0.5;
    if (inverseLattice) {
      const startFractional = start.clone().applyMatrix3(inverseLattice);
      const directionFractional = direction.clone().applyMatrix3(inverseLattice);
      for (let axis = 0; axis < 3; axis++) {
        const component = directionFractional.getComponent(axis);
        if (Math.abs(component) < 1e-10) continue;
        const boundary = component > 0 ? 1 : 0;
        const boundaryFraction = (boundary - startFractional.getComponent(axis)) / component;
        if (boundaryFraction > 1e-10 && boundaryFraction < fraction) fraction = boundaryFraction;
      }
    }
    return start.clone().addScaledVector(direction, fraction);
  };
  const imageTranslation = (offsetX, offsetY, offsetZ, lattice) => new THREE.Vector3(
    offsetX * lattice[0][0] + offsetY * lattice[1][0] + offsetZ * lattice[2][0],
    offsetX * lattice[0][1] + offsetY * lattice[1][1] + offsetZ * lattice[2][1],
    offsetX * lattice[0][2] + offsetY * lattice[1][2] + offsetZ * lattice[2][2],
  );
  const rebuild = () => {
    if (!state.scene || state.atoms.length >= MAX_BONDED_ATOMS) {
      reset();
      return;
    }
    ensureResources();
    const active = new Set();
    const inverseLattice = fractionalMatrix();
    const offsets = inverseLattice ? IMAGE_OFFSETS : [0];
    const lattice = state.lattice;
    const positions = state.atoms.map((atom) => state.meshes.get(atom.id)?.position);
    const maxThreshold = Math.max(...state.atoms.map((atom) => atomData(normalizeElement(atom.element))[2])) * 2 * BOND_THRESHOLD_FACTOR;
    const cellSize = Math.max(maxThreshold, MIN_BOND_LENGTH);
    const imageGrid = new Map();
    const addToGrid = (position, image) => {
      const cell = [
        Math.floor(position.x / cellSize),
        Math.floor(position.y / cellSize),
        Math.floor(position.z / cellSize),
      ];
      const key = cell.join(":");
      let bucket = imageGrid.get(key);
      if (!bucket) { bucket = []; imageGrid.set(key, bucket); }
      bucket.push({ ...image, cell });
    };
    for (let j = 0; j < state.atoms.length; j++) {
      if (!positions[j]) continue;
      for (const offsetX of offsets) for (const offsetY of offsets) for (const offsetZ of offsets) {
        const translation = inverseLattice ? imageTranslation(offsetX, offsetY, offsetZ, lattice) : new THREE.Vector3();
        addToGrid(positions[j].clone().add(translation), { j, offsetX, offsetY, offsetZ, translation });
      }
    }
    const nearbyImages = (position) => {
      const center = [
        Math.floor(position.x / cellSize),
        Math.floor(position.y / cellSize),
        Math.floor(position.z / cellSize),
      ];
      const result = [];
      for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) for (let dz = -1; dz <= 1; dz++) {
        result.push(...(imageGrid.get(`${center[0] + dx}:${center[1] + dy}:${center[2] + dz}`) || []));
      }
      return result;
    };
    for (let i = 0; i < state.atoms.length; i++) {
      const atomOne = state.atoms[i];
      const pointOne = positions[i];
      if (!pointOne) continue;
      for (const image of nearbyImages(pointOne)) {
        const { j, offsetX, offsetY, offsetZ, translation } = image;
        if (j < i) continue;
        const atomTwo = state.atoms[j];
        const pointTwo = positions[j];
        if (i === j && offsetX === 0 && offsetY === 0 && offsetZ === 0) continue;
        const threshold = (atomData(normalizeElement(atomOne.element))[2] + atomData(normalizeElement(atomTwo.element))[2]) * BOND_THRESHOLD_FACTOR;
        const imagePointTwo = pointTwo.clone().add(translation);
        const distance = pointOne.distanceTo(imagePointTwo);
        if (distance > threshold || distance < MIN_BOND_LENGTH) continue;
        const key = `${i}:${j}:${offsetX}:${offsetY}:${offsetZ}`;
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
        const imagePointOne = pointOne.clone().sub(translation);
        const midpointOne = halfBondEnd(pointOne, imagePointTwo, inverseLattice);
        const midpointTwo = halfBondEnd(pointTwo, imagePointOne, inverseLattice);
        positionHalfBond(entry.one, pointOne, midpointOne);
        positionHalfBond(entry.two, midpointTwo, pointTwo);
      }
    }
    for (const key of [...entries.keys()]) if (!active.has(key)) removeEntry(key);
  };

  return { rebuild, reset, dispose: reset };
}
