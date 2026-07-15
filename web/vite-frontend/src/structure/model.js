// Structure data/model helpers shared by the structure workbench and renderer.
// Rendering and pointer interaction stay in renderer.js.

export function parseStructure(value) {
  const text = String(value || "");
  const lines = text.trim().split(/\r?\n/);
  const count = Number.parseInt(lines[0], 10);
  if (!Number.isFinite(count) || count < 0) throw new Error("Structure is not valid ExtXYZ");
  const comment = lines[1] || "";
  const latticeMatch = comment.match(/Lattice="([^"]+)"/i);
  const latticeValues = latticeMatch?.[1]?.trim().split(/\s+/).map(Number);
  const lattice = latticeValues?.length === 9 && latticeValues.every(Number.isFinite)
    ? [latticeValues.slice(0, 3), latticeValues.slice(3, 6), latticeValues.slice(6, 9)] : null;
  const sites = lines.slice(2, count + 2).map((line, index) => {
    const columns = line.trim().split(/\s+/);
    if (columns.length < 4) throw new Error(`Invalid atom record at row ${index + 1}`);
    const xyz = columns.slice(1, 4).map(Number);
    if (xyz.some((item) => !Number.isFinite(item))) throw new Error(`Invalid coordinates at row ${index + 1}`);
    return { label: columns[0], species: [{ element: columns[0], occu: 1, oxidation_state: 0 }], xyz, abc: xyz };
  });
  return { sites, lattice: lattice ? { matrix: lattice } : undefined, properties: {} };
}

export function rendererAtoms(value) {
  return (value?.sites || []).map((site, index) => ({
    id: index, element: site.species?.[0]?.element || site.label || "X",
    x: Number(site.xyz?.[0] || 0), y: Number(site.xyz?.[1] || 0), z: Number(site.xyz?.[2] || 0),
  }));
}

export function rendererLattice(value) { return value?.lattice?.matrix || null; }

export function fractionalCoordinates(xyz, structure) {
  const matrix = structure?.lattice?.matrix;
  if (!matrix || matrix.length !== 3) return [...xyz];
  const [[a, b, c], [d, e, f], [g, h, i]] = matrix.map((row) => row.map(Number));
  const determinant = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g);
  if (!Number.isFinite(determinant) || Math.abs(determinant) < 1e-12) return [...xyz];
  const inverse = [
    [(e * i - f * h) / determinant, (c * h - b * i) / determinant, (b * f - c * e) / determinant],
    [(f * g - d * i) / determinant, (a * i - c * g) / determinant, (c * d - a * f) / determinant],
    [(d * h - e * g) / determinant, (b * g - a * h) / determinant, (a * e - b * d) / determinant],
  ];
  return inverse[0].map((_, column) => xyz.reduce((sum, value, row) => sum + value * inverse[row][column], 0));
}

export function serializeStructure(structure) {
  const sites = structure?.sites || [];
  const matrix = structure?.lattice?.matrix;
  const latticeField = matrix?.length === 3
    ? `Lattice="${matrix.flat().map(Number).join(" ")}" `
    : "";
  const pbcField = matrix?.length === 3 ? ' pbc="T T T"' : "";
  return `${sites.length}\n${latticeField}Properties=species:S:1:pos:R:3${pbcField}\n${sites.map((site) => {
    const element = site.species?.[0]?.element || site.label || "X";
    return `${element} ${site.xyz.map((value) => Number(value).toPrecision(15)).join(" ")}`;
  }).join("\n")}\n`;
}
