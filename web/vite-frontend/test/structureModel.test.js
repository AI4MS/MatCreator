import assert from "node:assert/strict";
import test from "node:test";

import {
  fractionalCoordinates,
  parseStructure,
  rendererAtoms,
  rendererLattice,
  serializeStructure,
} from "../src/structure/model.js";

const EXTXYZ = `2
Lattice="2 0 0 0 4 0 0 0 5" Properties=species:S:1:pos:R:3 pbc="T T T"
C 1 2 2.5
O 0 0 0
`;

test("parses ExtXYZ into the shared structure model", () => {
  const structure = parseStructure(EXTXYZ);

  assert.deepEqual(rendererLattice(structure), [[2, 0, 0], [0, 4, 0], [0, 0, 5]]);
  assert.deepEqual(rendererAtoms(structure), [
    { id: 0, element: "C", x: 1, y: 2, z: 2.5 },
    { id: 1, element: "O", x: 0, y: 0, z: 0 },
  ]);
  assert.deepEqual(fractionalCoordinates([1, 2, 2.5], structure), [0.5, 0.5, 0.5]);
});

test("serializes a structure to parseable ExtXYZ", () => {
  const original = parseStructure(EXTXYZ);
  const reparsed = parseStructure(serializeStructure(original));

  assert.deepEqual(rendererLattice(reparsed), rendererLattice(original));
  assert.deepEqual(rendererAtoms(reparsed), rendererAtoms(original));
});

test("rejects malformed atom records", () => {
  assert.throws(() => parseStructure("1\ncomment\nC nope 0 0\n"), /Invalid coordinates/);
  assert.throws(() => parseStructure("not-a-count\ncomment\n"), /not valid ExtXYZ/);
});

test("returns Cartesian coordinates for singular or absent lattices", () => {
  const xyz = [1, 2, 3];
  assert.deepEqual(fractionalCoordinates(xyz, {}), xyz);
  assert.notStrictEqual(fractionalCoordinates(xyz, {}), xyz);
  assert.deepEqual(fractionalCoordinates(xyz, { lattice: { matrix: [[1, 0, 0], [0, 0, 0], [0, 0, 1]] } }), xyz);
});
