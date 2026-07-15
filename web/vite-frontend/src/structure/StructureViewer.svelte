<script>
  import { onDestroy, onMount, tick, untrack } from "svelte";
  import { createStructureRenderer } from "./renderer.js";
  import { fractionalCoordinates, parseStructure, rendererAtoms, rendererLattice, serializeStructure } from "./model.js";

  // The structure is rotated so crystallographic Z points up. Apply that exact
  // rotation to the gizmo itself so its original X/Y/Z handles, labels, colors,
  // and click targets refer to the same crystal directions.
  let {
    structure_string,
    source_path,
    session_id,
    background_color,
    performance_mode = "quality",
    on_modified,
    on_generated,
  } = $props();
  let structure = $state();
  let selectedSites = $state([]);
  let viewerMode = $state("edit-atoms");
  let transformMode = $state("translate");
  let dirty = $state(false);
  let atomInspectorKey = $state("");
  let selectedElement = $state("");
  let periodicTableOpen = $state(false);
  let selectedX = $state("");
  let selectedY = $state("");
  let selectedZ = $state("");
  let localUndoStack = $state([]);
  let localRedoStack = $state([]);
  let initializedStructure;
  let currentStructureString = $state(untrack(() => structure_string));
  let workingStructureString = $derived(structure ? serializeStructure(structure) : currentStructureString);
  let currentPath = $state(untrack(() => source_path));
  let revision = $state(0);
  let workbenchOpen = $state(false);
  let operation = $state("surface");
  let busy = $state(false);
  let error = $state("");
  let status = $state("");

  let miller = $state("1, 0, 0");
  let layers = $state(4);
  let vacuum = $state(10);
  let supercellMatrix = $state([
    [2, 0, 0],
    [0, 2, 0],
    [0, 0, 1],
  ]);
  let moleculeSmiles = $state("O");
  let position = $state("0, 0, 0");
  let crystalSymbol = $state("Cu");
  let crystalStructure = $state("fcc");
  let latticeA = $state("");
  let perturbMagnitude = $state(0.1);
  let perturbMode = $state("random");
  let perturbSeed = $state(0);
  let secondaryPath = $state("");
  let interfaceDistance = $state(2);
  let filmMiller = $state("1, 0, 0");
  let substrateMiller = $state("1, 0, 0");
  let filmThickness = $state(3);
  let substrateThickness = $state(3);
  let interfaceInLayers = $state(true);
  let maxInterfaces = $state(10);
  let maxArea = $state(400);
  let maxLengthTolerance = $state(0.03);
  let maxAngleTolerance = $state(0.01);
  let maxAreaRatioTolerance = $state(0.09);
  let bidirectionalMatch = $state(false);
  let interfaceCandidates = $state([]);
  let selectedInterfaceId = $state("");
  let structureFiles = $state([]);
  let loadingStructureFiles = $state(false);
  let sketcherOpen = $state(false);
  let sketcherLoading = $state(false);
  let sketcherHost = $state();
  let ketcherApi = $state();
  let destroyKetcher;
  let viewerHost;
  let structureRenderer;
  let theme = $state("dark");

  const periodicElements = [
    ["H", "He"], ["Li", "Be", "B", "C", "N", "O", "F", "Ne"],
    ["Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar"],
    ["K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn", "Ga", "Ge", "As", "Se", "Br", "Kr"],
    ["Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn", "Sb", "Te", "I", "Xe"],
    ["Cs", "Ba", "La", "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg", "Tl", "Pb", "Bi", "Po", "At", "Rn"],
    ["Fr", "Ra", "Ac", "Th", "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm", "Md", "No", "Lr", "Rf", "Db", "Sg", "Bh", "Hs", "Mt", "Ds", "Rg", "Cn", "Nh", "Fl", "Mc", "Lv", "Ts", "Og"],
  ];
  const elementSymbols = new Set(periodicElements.flat());
  const periodicTable = [
    { symbol: "H", column: 1, row: 1 }, { symbol: "He", column: 18, row: 1 },
    { symbol: "Li", column: 1, row: 2 }, { symbol: "Be", column: 2, row: 2 }, { symbol: "B", column: 13, row: 2 }, { symbol: "C", column: 14, row: 2 }, { symbol: "N", column: 15, row: 2 }, { symbol: "O", column: 16, row: 2 }, { symbol: "F", column: 17, row: 2 }, { symbol: "Ne", column: 18, row: 2 },
    { symbol: "Na", column: 1, row: 3 }, { symbol: "Mg", column: 2, row: 3 }, { symbol: "Al", column: 13, row: 3 }, { symbol: "Si", column: 14, row: 3 }, { symbol: "P", column: 15, row: 3 }, { symbol: "S", column: 16, row: 3 }, { symbol: "Cl", column: 17, row: 3 }, { symbol: "Ar", column: 18, row: 3 },
    { symbol: "K", column: 1, row: 4 }, { symbol: "Ca", column: 2, row: 4 }, { symbol: "Sc", column: 3, row: 4 }, { symbol: "Ti", column: 4, row: 4 }, { symbol: "V", column: 5, row: 4 }, { symbol: "Cr", column: 6, row: 4 }, { symbol: "Mn", column: 7, row: 4 }, { symbol: "Fe", column: 8, row: 4 }, { symbol: "Co", column: 9, row: 4 }, { symbol: "Ni", column: 10, row: 4 }, { symbol: "Cu", column: 11, row: 4 }, { symbol: "Zn", column: 12, row: 4 }, { symbol: "Ga", column: 13, row: 4 }, { symbol: "Ge", column: 14, row: 4 }, { symbol: "As", column: 15, row: 4 }, { symbol: "Se", column: 16, row: 4 }, { symbol: "Br", column: 17, row: 4 }, { symbol: "Kr", column: 18, row: 4 },
    { symbol: "Rb", column: 1, row: 5 }, { symbol: "Sr", column: 2, row: 5 }, { symbol: "Y", column: 3, row: 5 }, { symbol: "Zr", column: 4, row: 5 }, { symbol: "Nb", column: 5, row: 5 }, { symbol: "Mo", column: 6, row: 5 }, { symbol: "Tc", column: 7, row: 5 }, { symbol: "Ru", column: 8, row: 5 }, { symbol: "Rh", column: 9, row: 5 }, { symbol: "Pd", column: 10, row: 5 }, { symbol: "Ag", column: 11, row: 5 }, { symbol: "Cd", column: 12, row: 5 }, { symbol: "In", column: 13, row: 5 }, { symbol: "Sn", column: 14, row: 5 }, { symbol: "Sb", column: 15, row: 5 }, { symbol: "Te", column: 16, row: 5 }, { symbol: "I", column: 17, row: 5 }, { symbol: "Xe", column: 18, row: 5 },
    { symbol: "Cs", column: 1, row: 6 }, { symbol: "Ba", column: 2, row: 6 }, { symbol: "La–Lu", column: 3, row: 6, series: true }, { symbol: "Hf", column: 4, row: 6 }, { symbol: "Ta", column: 5, row: 6 }, { symbol: "W", column: 6, row: 6 }, { symbol: "Re", column: 7, row: 6 }, { symbol: "Os", column: 8, row: 6 }, { symbol: "Ir", column: 9, row: 6 }, { symbol: "Pt", column: 10, row: 6 }, { symbol: "Au", column: 11, row: 6 }, { symbol: "Hg", column: 12, row: 6 }, { symbol: "Tl", column: 13, row: 6 }, { symbol: "Pb", column: 14, row: 6 }, { symbol: "Bi", column: 15, row: 6 }, { symbol: "Po", column: 16, row: 6 }, { symbol: "At", column: 17, row: 6 }, { symbol: "Rn", column: 18, row: 6 },
    { symbol: "Fr", column: 1, row: 7 }, { symbol: "Ra", column: 2, row: 7 }, { symbol: "Ac–Lr", column: 3, row: 7, series: true }, { symbol: "Rf", column: 4, row: 7 }, { symbol: "Db", column: 5, row: 7 }, { symbol: "Sg", column: 6, row: 7 }, { symbol: "Bh", column: 7, row: 7 }, { symbol: "Hs", column: 8, row: 7 }, { symbol: "Mt", column: 9, row: 7 }, { symbol: "Ds", column: 10, row: 7 }, { symbol: "Rg", column: 11, row: 7 }, { symbol: "Cn", column: 12, row: 7 }, { symbol: "Nh", column: 13, row: 7 }, { symbol: "Fl", column: 14, row: 7 }, { symbol: "Mc", column: 15, row: 7 }, { symbol: "Lv", column: 16, row: 7 }, { symbol: "Ts", column: 17, row: 7 }, { symbol: "Og", column: 18, row: 7 },
    ...["La", "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu"].map((symbol, index) => ({ symbol, column: index + 4, row: 9 })),
    ...["Ac", "Th", "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm", "Md", "No", "Lr"].map((symbol, index) => ({ symbol, column: index + 4, row: 10 })),
  ];

  const operationLabels = {
    surface: "Surface slab",
    supercell: "Supercell",
    molecule: "Add molecule",
    crystal: "Create crystal",
    perturb: "Perturb structure",
    interface: "Stack interface",
    vacuum: "Add vacuum",
  };

  const fileName = (path) => String(path || "").split(/[\\/]/).pop() || "Current structure";
  const activeSelectedSite = $derived(
    selectedSites.length === 1 ? structure?.sites?.[selectedSites[0]] : undefined,
  );

  const formatCoordinate = (value) => Number(value).toFixed(4);

  function snapshotStructure(value) {
    return $state.snapshot(value);
  }

  function pushLocalHistory() {
    if (!structure) return;
    localUndoStack = [...localUndoStack.slice(-19), snapshotStructure(structure)];
    localRedoStack = [];
  }

  function markModified() {
    dirty = true;
    on_modified?.();
  }

  function restoreLocalHistory(direction) {
    const source = direction === "undo" ? localUndoStack : localRedoStack;
    if (!structure || !source.length) return false;
    const restored = source.at(-1);
    const current = snapshotStructure(structure);
    if (direction === "undo") {
      localUndoStack = source.slice(0, -1);
      localRedoStack = [...localRedoStack.slice(-19), current];
    } else {
      localRedoStack = source.slice(0, -1);
      localUndoStack = [...localUndoStack.slice(-19), current];
    }
    structure = restored;
    selectedSites = [];
    return true;
  }

  function syncAtomInspector() {
    const site = activeSelectedSite;
    const index = selectedSites.length === 1 ? selectedSites[0] : "";
    const key = site ? `${index}|${site.species?.[0]?.element}|${site.xyz?.join(",")}` : "";
    if (key === atomInspectorKey) return;
    atomInspectorKey = key;
    selectedElement = site?.species?.[0]?.element || "";
    periodicTableOpen = false;
    selectedX = site ? formatCoordinate(site.xyz[0]) : "";
    selectedY = site ? formatCoordinate(site.xyz[1]) : "";
    selectedZ = site ? formatCoordinate(site.xyz[2]) : "";
  }

  function applyAtomInspector() {
    const siteIndex = selectedSites.length === 1 ? selectedSites[0] : -1;
    if (!structure?.sites?.[siteIndex]) return;
    const element = `${selectedElement.slice(0, 1).toUpperCase()}${selectedElement.slice(1).toLowerCase()}`;
    const xyz = [Number(selectedX), Number(selectedY), Number(selectedZ)];
    if (!elementSymbols.has(element)) {
      error = "Choose a valid element from the periodic-table list";
      return;
    }
    if (xyz.some((value) => !Number.isFinite(value))) {
      error = "Cartesian coordinates must be finite numbers";
      return;
    }
    error = "";
    pushLocalHistory();
    const abc = fractionalCoordinates(xyz, structure);
    structure = {
      ...structure,
      sites: structure.sites.map((site, index) => index === siteIndex
        ? {
            ...site,
            species: [{ element, occu: 1, oxidation_state: 0 }],
            label: element,
            xyz,
            abc,
          }
        : site),
    };
  }

  function deleteSelectedAtoms() {
    const deleted = new Set(selectedSites.filter((index) => structure?.sites?.[index]));
    if (!deleted.size) return;
    const bonds = structure.properties?.bonds || [];
    pushLocalHistory();
    const nextIndex = new Map();
    let offset = 0;
    structure.sites.forEach((_, index) => {
      if (deleted.has(index)) offset += 1;
      else nextIndex.set(index, index - offset);
    });
    const remainingBonds = bonds
      .filter((bond) => !deleted.has(bond.site_idx_1) && !deleted.has(bond.site_idx_2))
      .map((bond) => ({
        ...bond,
        site_idx_1: nextIndex.get(bond.site_idx_1),
        site_idx_2: nextIndex.get(bond.site_idx_2),
      }));
    structure = {
      ...structure,
      sites: structure.sites.filter((_, index) => !deleted.has(index)),
      properties: { ...(structure.properties || {}), bonds: remainingBonds },
    };
    selectedSites = [];
  }

  // Adapted from AtomClay's createAtomAtCenter action. The lattice center is
  // used for crystals; molecules use the Cartesian centroid (or origin).
  function createAtomAtCenter() {
    if (!structure) return;
    pushLocalHistory();
    const lattice = structure.lattice?.matrix;
    const xyz = lattice?.length === 3
      ? [0, 1, 2].map((axis) => lattice.reduce((sum, row) => sum + Number(row[axis]), 0) / 2)
      : structure.sites?.length
        ? [0, 1, 2].map((axis) => structure.sites.reduce((sum, site) => sum + Number(site.xyz[axis]), 0) / structure.sites.length)
        : [0, 0, 0];
    const element = elementSymbols.has(selectedElement) ? selectedElement : "C";
    const site = { label: element, species: [{ element, occu: 1, oxidation_state: 0 }], xyz, abc: fractionalCoordinates(xyz, structure) };
    structure = { ...structure, sites: [...(structure.sites || []), site] };
    selectedSites = [structure.sites.length - 1];
    transformMode = "translate";
  }

  async function saveLocalEdits() {
    if (!dirty || busy) return;
    busy = true;
    error = "";
    status = "Saving edited structure…";
    try {
      const response = await fetch("/api/structure/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id, structure_string: serializeStructure(structure) }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      currentPath = data.path;
      dirty = false;
      status = `${data.formula} · ${data.n_atoms} atoms · saved as ${fileName(data.path)}`;
      on_generated?.(data);
    } catch (saveError) {
      error = String(saveError?.message || saveError);
      status = "";
    } finally {
      busy = false;
    }
  }

  async function selectOperation(nextOperation) {
    if (operation === nextOperation && workbenchOpen) {
      workbenchOpen = false;
      return;
    }
    operation = nextOperation;
    error = "";
    status = "";
    workbenchOpen = true;
    if (nextOperation === "interface") await loadStructureFiles();
  }

  async function loadStructureFiles() {
    loadingStructureFiles = true;
    try {
      if (!session_id) throw new Error("Open a session before building an interface");
      // Reuse the established session file API so this remains compatible with
      // already-running MatCreator backends during a frontend update.
      const response = await fetch(`/api/sessions/${encodeURIComponent(session_id)}/files`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      const listed = (data.files || [])
        .filter((item) => item.path !== currentPath)
        .map((item) => ({ ...item, relative_path: item.name || fileName(item.path) }));
      structureFiles = listed;
      if (!listed.some((item) => item.path === secondaryPath)) {
        secondaryPath = listed[0]?.path || "";
      }
    } catch (fileError) {
      structureFiles = [];
      secondaryPath = "";
      error = `Could not list working-directory structures: ${fileError?.message || fileError}`;
    } finally {
      loadingStructureFiles = false;
    }
  }

  const parseVector = (value, length = 3) => {
    const parsed = String(value).split(/[;,\s]+/).filter(Boolean).map(Number);
    if (parsed.length !== length || parsed.some((item) => !Number.isFinite(item))) {
      throw new Error(`Expected ${length} numeric values`);
    }
    return parsed;
  };

  const parseSupercellMatrix = () => {
    const parsed = supercellMatrix.map((row) => row.map(Number));
    if (parsed.some((row) => row.length !== 3 || row.some((value) => !Number.isInteger(value)))) {
      throw new Error("Each supercell matrix entry must be an integer");
    }
    return parsed;
  };

  function applyGeneratedStructure(data) {
    currentStructureString = data.structure_string;
    currentPath = data.path;
    initializedStructure = undefined;
    structure = parseStructure(currentStructureString);
    dirty = false;
    revision += 1;
    status = `${data.formula} · ${data.n_atoms} atoms · saved as ${data.path.split(/[\\/]/).pop()}`;
    on_generated?.(data);
    if (operation === "interface") void loadStructureFiles();
  }

  async function openSketcher() {
    sketcherOpen = true;
    sketcherLoading = true;
    error = "";
    await tick();
    try {
      const { mountKetcher } = await import("../KetcherBridge.jsx");
      destroyKetcher?.();
      destroyKetcher = await mountKetcher(sketcherHost, {
        initialSmiles: moleculeSmiles,
        onReady: (api) => {
          ketcherApi = api;
          sketcherLoading = false;
        },
        onError: (message) => {
          error = message;
          sketcherLoading = false;
        },
      });
    } catch (sketcherError) {
      error = `Could not load molecule editor: ${sketcherError?.message || sketcherError}`;
      sketcherLoading = false;
    }
  }

  function closeSketcher() {
    sketcherOpen = false;
    ketcherApi = undefined;
    destroyKetcher?.();
    destroyKetcher = undefined;
  }

  async function useDrawnMolecule() {
    if (!ketcherApi) return;
    try {
      moleculeSmiles = await ketcherApi.getSmiles();
      closeSketcher();
    } catch (sketcherError) {
      error = `Could not export drawing: ${sketcherError?.message || sketcherError}`;
    }
  }

  onDestroy(() => destroyKetcher?.());

  $effect(() => {
    const nextBackground = theme === "light" ? "#f8fbff" : "#06080f";
    viewerHost?.style.setProperty("background", nextBackground);
    structureRenderer?.setTheme?.(theme);
  });

  onMount(() => {
    theme = background_color === "#f8fbff" ? "light" : "dark";
    const onThemeChange = (event) => {
      theme = event.detail === "light" ? "light" : "dark";
      structureRenderer?.setTheme?.(theme);
    };
    window.addEventListener("matcreator-theme-change", onThemeChange);
    try {
      structure = parseStructure(currentStructureString);
      structureRenderer = createStructureRenderer().init(viewerHost, {
        background: background_color || "#06080f",
        onSelect: (id, additive) => {
          if (id === null) selectedSites = additive ? selectedSites : [];
          else selectedSites = additive
            ? (selectedSites.includes(id) ? selectedSites.filter((item) => item !== id) : [...selectedSites, id])
            : [id];
        },
        onMove: (movedAtoms) => {
          if (!structure?.sites || !movedAtoms?.some(({ id }) => structure.sites[id])) return;
          pushLocalHistory();
          structure = {
            ...structure,
            sites: structure.sites.map((site, index) => {
              const movedAtom = movedAtoms.find(({ id }) => id === index);
              if (!movedAtom) return site;
              const xyz = movedAtom.xyz.map(Number);
              return { ...site, xyz, abc: fractionalCoordinates(xyz, structure) };
            }),
          };
        },
        onBoxSelect: (ids, additive) => {
          selectedSites = additive ? [...new Set([...selectedSites, ...ids])] : ids;
        },
      });
      structureRenderer.sync({ atoms: rendererAtoms(structure), lattice: rendererLattice(structure) });
    } catch (rendererError) {
      error = `Could not load structure viewer: ${rendererError?.message || rendererError}`;
    }

    const suppressBuiltInAtomShortcuts = (event) => {
      const target = event.target;
      if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) return;
      if (viewerMode === "edit-atoms" && (event.ctrlKey || event.metaKey)) {
        const key = event.key.toLowerCase();
        const direction = key === "z" && !event.shiftKey ? "undo"
          : key === "y" || (key === "z" && event.shiftKey) ? "redo" : null;
        if (direction && restoreLocalHistory(direction)) {
          event.preventDefault();
          event.stopImmediatePropagation();
          return;
        }
      }
      if (viewerMode !== "edit-atoms" || !selectedSites.length || event.ctrlKey || event.metaKey || event.altKey) return;
      if (["a", "e", "delete", "backspace"].includes(event.key.toLowerCase())) {
        event.preventDefault();
        event.stopImmediatePropagation();
        if (["delete", "backspace"].includes(event.key.toLowerCase())) deleteSelectedAtoms();
      }
    };
    window.addEventListener("keydown", suppressBuiltInAtomShortcuts, true);
    return () => {
      window.removeEventListener("keydown", suppressBuiltInAtomShortcuts, true);
      window.removeEventListener("matcreator-theme-change", onThemeChange);
      structureRenderer?.dispose();
    };
  });

  $effect(() => {
    void structure;
    structureRenderer?.sync({ atoms: rendererAtoms(structure), lattice: rendererLattice(structure) });
  });

  $effect(() => {
    void selectedSites;
    syncAtomInspector();
    structureRenderer?.select(selectedSites);
  });

  $effect(() => {
    structureRenderer?.setTransformMode(transformMode);
  });

  function operationParams() {
    if (operation === "surface") return { miller: parseVector(miller), layers, vacuum };
    if (operation === "supercell") return { matrix: parseSupercellMatrix() };
    if (operation === "molecule") return {
      smiles: moleculeSmiles,
      position: parseVector(position),
      append: true,
      optimize: true,
    };
    if (operation === "crystal") return {
      symbol: crystalSymbol,
      crystal_structure: crystalStructure,
      a: latticeA || null,
      cubic: true,
    };
    if (operation === "perturb") return {
      magnitude: Number(perturbMagnitude),
      mode: perturbMode,
      seed: Number(perturbSeed),
    };
    if (operation === "vacuum") return { axis: 2, vacuum };
    return {};
  }

  async function buildModel() {
    busy = true;
    error = "";
    status = "Building…";
    try {
      const response = await fetch("/api/structure/model", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: currentPath,
          structure_string: workingStructureString,
          session_id,
          operation,
          params: operationParams(),
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      applyGeneratedStructure(data);
    } catch (buildError) {
      error = String(buildError?.message || buildError);
      status = "";
    } finally {
      busy = false;
    }
  }

  async function generateInterfaces() {
    busy = true;
    error = "";
    status = "Generating lattice matches…";
    interfaceCandidates = [];
    selectedInterfaceId = "";
    try {
      const response = await fetch("/api/structure/interfaces", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: currentPath,
          structure_string: workingStructureString,
          secondary_path: secondaryPath,
          session_id,
          params: {
            film_miller: parseVector(filmMiller),
            substrate_miller: parseVector(substrateMiller),
            gap: interfaceDistance,
            vacuum_over_film: vacuum,
            film_thickness: filmThickness,
            substrate_thickness: substrateThickness,
            in_layers: interfaceInLayers,
            max_interfaces: maxInterfaces,
            max_area: maxArea,
            max_length_tol: maxLengthTolerance,
            max_angle_tol: maxAngleTolerance,
            max_area_ratio_tol: maxAreaRatioTolerance,
            bidirectional: bidirectionalMatch,
          },
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      interfaceCandidates = data.interfaces || [];
      selectedInterfaceId = interfaceCandidates[0]?.id || "";
      status = interfaceCandidates.length
        ? `${interfaceCandidates.length} candidate${interfaceCandidates.length === 1 ? "" : "s"} generated`
        : "No matching interfaces found. Try relaxed tolerances.";
    } catch (interfaceError) {
      error = String(interfaceError?.message || interfaceError);
      status = "";
    } finally {
      busy = false;
    }
  }

  async function saveSelectedInterface() {
    const candidate = interfaceCandidates.find((item) => item.id === selectedInterfaceId);
    if (!candidate) return;
    busy = true;
    error = "";
    try {
      const response = await fetch("/api/structure/interfaces/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id, structure_string: candidate.structure_string }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      applyGeneratedStructure(data);
    } catch (saveError) {
      error = String(saveError?.message || saveError);
    } finally {
      busy = false;
    }
  }

  $effect(() => {
    if (!structure) return;
    if (!initializedStructure) {
      initializedStructure = structure;
      return;
    }
    if (structure !== initializedStructure) {
      initializedStructure = structure;
      markModified();
    }
  });
</script>

<div class="structure-viewer-embed">
  <div class="structure-renderer-host" bind:this={viewerHost} aria-label="Interactive structure viewer"></div>
  <div class="structure-interaction-hint">Orbit · Zoom · Shift-drag select · Axis widget align</div>

  <nav class="model-tools" aria-label="Structure modeling tools">
    <button disabled={!localUndoStack.length} title="Undo local atom edit (Ctrl+Z)" aria-label="Undo local atom edit" onclick={() => restoreLocalHistory("undo")}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 7 4 12l5 5M4 12h10a6 6 0 0 1 0 12" /></svg>
    </button>
    <button disabled={!localRedoStack.length} title="Redo local atom edit (Ctrl+Y)" aria-label="Redo local atom edit" onclick={() => restoreLocalHistory("redo")}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 7 5 5-5 5m5-5H10a6 6 0 0 0 0 12" /></svg>
    </button>
    <button disabled={!dirty || busy} title="Save edited structure" aria-label="Save edited structure" onclick={saveLocalEdits}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4h12l2 2v14H5V4Zm3 0v6h8V4M8 20v-6h8v6" /></svg>
    </button>
    <button class:active={viewerMode === "edit-atoms"} title="Atom editor" aria-label="Atom editor" onclick={() => { viewerMode = "edit-atoms"; selectedSites = []; }}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 20 4.5-1 10-10-3.5-3.5-10 10L4 20Zm9-12 3.5 3.5M14 4l2-2 4 4-2 2" /></svg>
    </button>
    <button title="Add atom at structure center" aria-label="Add atom at structure center" onclick={createAtomAtCenter}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10" cy="12" r="5" /><path d="M18 5v6M15 8h6" /></svg>
    </button>
    <button class:active={transformMode === "translate"} disabled={!selectedSites.length} title="Move selected atoms" aria-label="Move selected atoms" onclick={() => transformMode = "translate"}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v18M3 12h18m-9-9-3 3m3-3 3 3m-3 15-3-3m3 3 3-3M3 12l3-3m-3 3 3 3m15-3-3-3m3 3-3 3" /></svg>
    </button>
    <button class:active={transformMode === "rotate"} disabled={!selectedSites.length} title="Rotate selected atoms" aria-label="Rotate selected atoms" onclick={() => transformMode = "rotate"}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 8a8 8 0 1 0 1 6M19 8V3m0 5h-5" /></svg>
    </button>
    <button class:active={workbenchOpen && operation === "surface"} title="Surface slab" aria-label="Surface slab" onclick={() => void selectOperation("surface")}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 17h16M6 13h12M8 9h8M10 5h4" /></svg>
    </button>
    <button class:active={workbenchOpen && operation === "supercell"} title="Supercell" aria-label="Supercell" onclick={() => void selectOperation("supercell")}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="1" /><path d="M12 4v16M4 12h16" /></svg>
    </button>
    <button class:active={workbenchOpen && operation === "molecule"} title="Add molecule" aria-label="Add molecule" onclick={() => void selectOperation("molecule")}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7.2 15 3.2-4.5m3.2 0 3.2 4.5" /><circle cx="5" cy="16" r="2.5" /><circle cx="12" cy="8" r="2.5" /><circle cx="19" cy="16" r="2.5" /></svg>
    </button>
    <button class:active={workbenchOpen && operation === "crystal"} title="Create crystal" aria-label="Create crystal" onclick={() => void selectOperation("crystal")}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Zm0 9 8-4.5M12 12 4 7.5M12 12v9" /></svg>
    </button>
    <button class:active={workbenchOpen && operation === "perturb"} title="Perturb structure" aria-label="Perturb structure" onclick={() => void selectOperation("perturb")}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M12 5v14" /><path d="m8 8 1.5 1.5M16 8l-1.5 1.5M8 16l1.5-1.5M16 16l-1.5-1.5" /></svg>
    </button>
    <button class:active={workbenchOpen && operation === "interface"} title="Stack interface" aria-label="Stack interface" onclick={() => void selectOperation("interface")}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 7 8-4 8 4-8 4-8-4Zm0 10 8 4 8-4M4 12l8 4 8-4" /></svg>
    </button>
    <button class:active={workbenchOpen && operation === "vacuum"} title="Add vacuum" aria-label="Add vacuum" onclick={() => void selectOperation("vacuum")}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 18h16M12 9v6m-3-3 3-3 3 3m-6 0 3 3 3-3" /></svg>
    </button>
  </nav>

  {#if workbenchOpen}
    <aside class="model-workbench" class:interface-workbench={operation === "interface"}>
      <header><strong>{operationLabels[operation]}</strong><button class="close-button" title="Close" aria-label="Close modeling tool" onclick={() => workbenchOpen = false}>×</button></header>

      {#if operation === "surface"}
        <label>Miller indices <input bind:value={miller} /></label>
        <div class="field-row"><label>Layers <input type="number" min="1" bind:value={layers} /></label><label>Vacuum Å <input type="number" min="0" step="0.5" bind:value={vacuum} /></label></div>
      {:else if operation === "supercell"}
        <fieldset class="matrix-fieldset">
          <legend>Transformation matrix</legend>
          <div class="matrix-grid">
            {#each supercellMatrix as row, rowIndex}
              {#each row as _, columnIndex}
                <input
                  type="number"
                  step="1"
                  aria-label={`Matrix row ${rowIndex + 1}, column ${columnIndex + 1}`}
                  bind:value={supercellMatrix[rowIndex][columnIndex]}
                />
              {/each}
            {/each}
          </div>
        </fieldset>
        <small>Enter an integer 3 × 3 transformation matrix. The default doubles the a and b lattice vectors.</small>
      {:else if operation === "molecule"}
        <label>SMILES <textarea rows="2" bind:value={moleculeSmiles} placeholder="CCO, c1ccccc1, CC(=O)O…"></textarea></label>
        <button class="secondary-button draw-button" type="button" onclick={openSketcher}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 20 4.5-1 10-10-3.5-3.5-10 10L4 20Zm9-12 3.5 3.5M14 4l2-2 4 4-2 2" /></svg>
          Draw molecule
        </button>
        <label>Center position Å <input bind:value={position} /></label>
        <small>RDKit generates explicit hydrogens, ETKDG 3D coordinates, and an MMFF/UFF-optimized geometry.</small>
      {:else if operation === "crystal"}
        <div class="field-row"><label>Element <input bind:value={crystalSymbol} /></label><label>Prototype <select bind:value={crystalStructure}><option>fcc</option><option>bcc</option><option>hcp</option><option>diamond</option><option>rocksalt</option><option>zincblende</option></select></label></div>
        <label>Lattice a Å (optional) <input type="number" step="0.01" bind:value={latticeA} /></label>
      {:else if operation === "perturb"}
        <div class="field-row"><label>Displacement Å <input type="number" min="0" step="0.01" bind:value={perturbMagnitude} /></label><label>Random seed <input type="number" step="1" bind:value={perturbSeed} /></label></div>
        <label>Distribution <select bind:value={perturbMode}><option value="random">Uniform random</option><option value="gaussian">Gaussian</option><option value="scaled_random">Covalent-radius scaled</option></select></label>
        <small>Uses MatCraft Kit’s reproducible perturbation builder. The same seed recreates the same displacement.</small>
      {:else if operation === "interface"}
        <div class="interface-columns">
          <section>
            <strong>Film</strong>
            <label>Current structure <input value={fileName(currentPath)} readonly title={currentPath} /></label>
            <label>Miller indices <input bind:value={filmMiller} /></label>
            <label>Thickness <input type="number" min="1" step="1" bind:value={filmThickness} /></label>
          </section>
          <section>
            <strong>Substrate</strong>
            <label>Structure file
              <select bind:value={secondaryPath} disabled={loadingStructureFiles || !structureFiles.length}>
                {#if loadingStructureFiles}
                  <option value="">Loading structures…</option>
                {:else if !structureFiles.length}
                  <option value="">No other structure files found</option>
                {:else}
                  {#each structureFiles as structureFile}
                    <option value={structureFile.path}>{structureFile.relative_path}</option>
                  {/each}
                {/if}
              </select>
            </label>
            <label>Miller indices <input bind:value={substrateMiller} /></label>
            <label>Thickness <input type="number" min="1" step="1" bind:value={substrateThickness} /></label>
          </section>
        </div>
        <div class="field-row"><label>Gap Å <input type="number" min="0" step="0.1" bind:value={interfaceDistance} /></label><label>Vacuum Å <input type="number" min="0" step="0.5" bind:value={vacuum} /></label></div>
        <div class="field-row"><label>Max area Å² <input type="number" min="1" step="10" bind:value={maxArea} /></label><label>Max candidates <input type="number" min="1" max="100" step="1" bind:value={maxInterfaces} /></label></div>
        <details class="advanced-options">
          <summary>Advanced matching tolerances</summary>
          <div class="field-row"><label>Length tolerance <input type="number" min="0" step="0.01" bind:value={maxLengthTolerance} /></label><label>Angle tolerance <input type="number" min="0" step="0.01" bind:value={maxAngleTolerance} /></label></div>
          <label>Area ratio tolerance <input type="number" min="0" step="0.01" bind:value={maxAreaRatioTolerance} /></label>
          <label class="check-label"><input type="checkbox" bind:checked={interfaceInLayers} /> Thickness values are layer counts</label>
          <label class="check-label"><input type="checkbox" bind:checked={bidirectionalMatch} /> Bidirectional ZSL matching</label>
        </details>

        <button class="build-button" disabled={busy || !secondaryPath} onclick={generateInterfaces}>{busy ? "Generating…" : "Generate candidates"}</button>
        {#if interfaceCandidates.length}
          <div class="interface-candidates" role="listbox" aria-label="Interface candidates">
            {#each interfaceCandidates as candidate}
              <button
                type="button"
                class:selected={selectedInterfaceId === candidate.id}
                onclick={() => selectedInterfaceId = candidate.id}
                role="option"
                aria-selected={selectedInterfaceId === candidate.id}
              >
                <strong>{candidate.id}</strong>
                <span>ε {candidate.von_mises_strain == null ? "—" : `${(candidate.von_mises_strain * 100).toFixed(2)}%`}</span>
                <span>{candidate.area.toFixed(1)} Å²</span>
                <span>{candidate.n_atoms} atoms</span>
                <span>T{candidate.termination_index}</span>
              </button>
            {/each}
          </div>
          <button class="secondary-button" disabled={busy || !selectedInterfaceId} onclick={saveSelectedInterface}>Save and open selected</button>
        {/if}
      {:else if operation === "vacuum"}
        <label>Z vacuum Å <input type="number" min="0" step="0.5" bind:value={vacuum} /></label>
      {/if}

      {#if operation !== "interface"}
        <button class="build-button" disabled={busy || (operation === "molecule" && !moleculeSmiles.trim())} onclick={buildModel}>{busy ? "Building…" : "Build and save"}</button>
      {/if}
      {#if error}<div class="model-error">{error}</div>{/if}
      {#if status}<div class="model-status">{status}</div>{/if}
      <p class="model-note">Atom movement, deletion, and element changes are available in this local viewer. Each builder creates a new ExtXYZ file.</p>
    </aside>
  {/if}

  {#if viewerMode === "edit-atoms" && activeSelectedSite}
    <aside class="atom-inspector" aria-label="Selected atom editor">
      <header>
        <div><strong>Atom {selectedSites[0] + 1}</strong><span>Cartesian editor</span></div>
        <button class="close-button" title="Deselect atom" aria-label="Deselect atom" onclick={() => selectedSites = []}>×</button>
      </header>
      <div class="element-picker">
        <span>Element</span>
        <button class="element-picker-button" type="button" aria-expanded={periodicTableOpen} onclick={() => periodicTableOpen = !periodicTableOpen}>
          <strong>{selectedElement}</strong><span>Choose from periodic table</span>
        </button>
        {#if periodicTableOpen}
          <div class="periodic-table" role="grid" aria-label="Periodic table">
            {#each periodicTable as element}
              <button
                type="button"
                class:selected={selectedElement === element.symbol}
                class:series={element.series}
                style={`grid-column: ${element.column}; grid-row: ${element.row};`}
                title={element.series ? `${element.symbol}: choose from the series below` : element.symbol}
                aria-label={element.symbol}
                onclick={() => {
                  if (element.series) return;
                  selectedElement = element.symbol;
                  periodicTableOpen = false;
                }}
              >{element.symbol}</button>
            {/each}
          </div>
        {/if}
      </div>
      <div class="coordinate-grid">
        <label>X Å <input type="number" step="0.001" bind:value={selectedX} /></label>
        <label>Y Å <input type="number" step="0.001" bind:value={selectedY} /></label>
        <label>Z Å <input type="number" step="0.001" bind:value={selectedZ} /></label>
      </div>
      <p>Drag the colored transform arrows to move the selected atom along the displayed crystal axes.</p>
      <div class="inspector-actions">
        <button class="secondary-button" type="button" onclick={deleteSelectedAtoms}>Delete atom</button>
        <button class="build-button" type="button" onclick={applyAtomInspector}>Apply</button>
      </div>
    </aside>
  {:else if viewerMode === "edit-atoms" && selectedSites.length > 1}
    <aside class="atom-inspector atom-selection-summary">
      <header><div><strong>{selectedSites.length} atoms selected</strong><span>Transform group</span></div><button class="close-button" title="Clear selection" aria-label="Clear selection" onclick={() => selectedSites = []}>×</button></header>
      <p>Shift-drag on empty space to box-select. Ctrl/Cmd-click adds or removes individual atoms. Select one atom to edit its element and Cartesian coordinates.</p>
      <button class="secondary-button" type="button" onclick={deleteSelectedAtoms}>Delete selected atoms</button>
    </aside>
  {/if}

  {#if sketcherOpen}
    <div class="sketcher-overlay" role="dialog" aria-modal="true" aria-label="Molecule drawing editor">
      <div class="sketcher-dialog">
        <header><strong>Molecule Editor</strong><button class="close-button" aria-label="Close molecule editor" onclick={closeSketcher}>×</button></header>
        <div class="sketcher-host" bind:this={sketcherHost}></div>
        {#if sketcherLoading}<div class="sketcher-loading">Loading Ketcher…</div>{/if}
        <footer>
          <button class="secondary-button" onclick={closeSketcher}>Cancel</button>
          <button class="build-button" disabled={sketcherLoading || !ketcherApi} onclick={useDrawnMolecule}>Use drawing</button>
        </footer>
      </div>
    </div>
  {/if}
</div>

<style>
  .structure-viewer-embed {
    --border-radius: 3pt;
    --ctrl-btn-icon-size: clamp(0.7rem, 2cqmin, 0.85rem);
    --z-index-overlay-controls: 100000000;
    --z-index-overlay-nav: 100000001;
    --z-index-overlay-dialog: 100000002;
    --z-index-overlay-options: 100000003;
    width: 100%;
    height: 100%;
    min-height: 0;
    position: relative;
    overflow: hidden;
  }
  .structure-renderer-host { position: absolute; inset: 0; min-height: 0; overflow: hidden; }
  .structure-interaction-hint { position: absolute; z-index: 840; left: 50%; bottom: 12px; transform: translateX(-50%); width: max-content; max-width: calc(100% - 140px); padding: 5px 8px; border: 1px solid color-mix(in srgb, var(--border) 80%, transparent); border-radius: 7px; color: var(--muted); background: rgba(var(--panel-rgb), .72); font: 11px/1.3 'Manrope', system-ui, sans-serif; white-space: nowrap; pointer-events: none; }

  .structure-viewer-embed :global(button) {
    color: inherit;
    cursor: pointer;
    border: none;
  }

  .model-tools {
    position: absolute;
    z-index: 900;
    top: 10px;
    left: 10px;
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    max-width: calc(100% - 20px);
    padding: 4px;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: rgba(var(--panel-rgb), 0.9);
    box-shadow: var(--panel-shadow);
    backdrop-filter: blur(10px);
  }
  .model-tools button {
    display: grid;
    place-items: center;
    width: 30px;
    height: 30px;
    padding: 0;
    border: 1px solid transparent !important;
    border-radius: 8px;
    background: transparent;
    color: var(--muted);
    transition: color 0.16s ease, border-color 0.16s ease, background 0.16s ease, transform 0.16s ease;
  }
  .model-tools svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }
  .model-tools button:hover, .model-tools button:focus-visible, .model-tools button.active {
    color: var(--accent);
    border-color: color-mix(in srgb, var(--accent) 35%, transparent) !important;
    background: color-mix(in srgb, var(--accent) 10%, transparent);
    transform: translateY(-1px);
  }
  .model-tools button:disabled { cursor: default; opacity: 0.4; }
  .model-tools button:disabled:hover { transform: none; }
  .model-tools button:focus-visible { outline: 2px solid color-mix(in srgb, var(--accent) 30%, transparent); outline-offset: 1px; }
  .model-tools button:first-child svg { width: 17px; height: 17px; }
  :global(.measure-mode-dropdown .view-mode-option[title="Edit Atoms"]), :global(.measure-mode-dropdown .view-mode-option[title="Edit Bonds"]) { display: none !important; }
  .model-workbench {
    position: absolute;
    z-index: 900;
    top: 58px;
    left: 10px;
    width: min(330px, calc(100% - 20px));
    max-height: calc(100% - 60px);
    overflow: auto;
    box-sizing: border-box;
    padding: 14px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    color: var(--text);
    background: rgba(var(--panel-rgb), 0.97);
    box-shadow: var(--popup-shadow);
    backdrop-filter: blur(16px);
    font: 12px/1.4 'Manrope', system-ui, -apple-system, sans-serif;
  }
  .model-workbench.interface-workbench { width: min(560px, calc(100% - 20px)); }
  .model-workbench header { display: flex; justify-content: space-between; align-items: center; margin: -2px 0 11px; padding-bottom: 10px; border-bottom: 1px solid var(--border); }
  .model-workbench header strong { font-size: 13px; color: var(--text); }
  .atom-inspector { position: absolute; z-index: 900; top: 58px; right: 10px; width: min(280px, calc(100% - 20px)); box-sizing: border-box; padding: 14px; border: 1px solid var(--border); border-radius: var(--radius); color: var(--text); background: rgba(var(--panel-rgb), 0.97); box-shadow: var(--popup-shadow); backdrop-filter: blur(16px); font: 12px/1.4 'Manrope', system-ui, -apple-system, sans-serif; }
  .atom-inspector header { display: flex; justify-content: space-between; align-items: flex-start; margin: -2px 0 10px; padding-bottom: 10px; border-bottom: 1px solid var(--border); }
  .atom-inspector header strong { display: block; font-size: 13px; }
  .atom-inspector header span { color: var(--muted); font-size: 11px; }
  .atom-inspector label { display: grid; gap: 5px; margin: 9px 0; color: var(--muted); font-weight: 550; }
  .atom-inspector :is(input, select) { width: 100%; min-width: 0; box-sizing: border-box; padding: 7px 8px; border: 1px solid var(--border); border-radius: 8px; color: var(--text); background: var(--subtle-surface); font: inherit; font-weight: 400; outline: none; }
  .atom-inspector :is(input, select):focus { border-color: var(--accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 12%, transparent); }
  .element-picker { position: relative; display: grid; gap: 5px; margin: 9px 0; color: var(--muted); font-weight: 550; }
  .element-picker-button { display: flex; align-items: center; justify-content: space-between; gap: 8px; width: 100%; padding: 7px 8px; border: 1px solid var(--border) !important; border-radius: 8px; color: var(--text); background: var(--subtle-surface); text-align: left; font: inherit; }
  .element-picker-button:hover { border-color: var(--accent) !important; }
  .element-picker-button strong { display: grid; place-items: center; width: 24px; height: 24px; border-radius: 6px; color: var(--bg); background: var(--accent); font-size: 12px; }
  .element-picker-button span { margin-left: auto; color: var(--muted); font-size: 11px; font-weight: 400; }
  .periodic-table { position: absolute; z-index: 2; top: calc(100% + 5px); right: 0; display: grid; grid-template-columns: repeat(18, minmax(0, 1fr)); grid-template-rows: repeat(7, 23px) 8px repeat(2, 23px); gap: 2px; width: min(510px, calc(100vw - 42px)); padding: 7px; border: 1px solid var(--border); border-radius: 9px; background: var(--panel); box-shadow: var(--popup-shadow); }
  .periodic-table button { min-width: 0; padding: 0; border: 1px solid transparent !important; border-radius: 4px; color: var(--text); background: var(--subtle-surface); font: 600 8px/1 system-ui, sans-serif; }
  .periodic-table button:hover, .periodic-table button.selected { border-color: var(--accent) !important; color: var(--bg); background: var(--accent); }
  .periodic-table button.series { color: var(--muted); font-size: 7px; cursor: default; }
  .periodic-table button.series:hover { border-color: transparent !important; color: var(--muted); background: var(--subtle-surface); }
  .coordinate-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }
  .coordinate-grid label { min-width: 0; }
  .coordinate-grid input { padding-inline: 4px !important; text-align: center; }
  .atom-inspector p { margin: 10px 0; color: var(--muted); font-size: 11px; }
  .inspector-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 7px; }
  .inspector-actions .build-button { margin: 0; }
  .atom-selection-summary { top: 58px; }
  .close-button { display: grid; place-items: center; width: 24px; height: 24px; border-radius: 999px; color: var(--muted); background: transparent; font-size: 18px; line-height: 1; }
  .close-button:hover { color: #f87171; background: rgba(248, 113, 113, 0.14); }
  .model-workbench label { display: grid; gap: 5px; margin: 9px 0; color: var(--muted); font-weight: 550; }
  .model-workbench :is(input, select, textarea) { width: 100%; box-sizing: border-box; padding: 7px 8px; border: 1px solid var(--border); border-radius: 8px; color: var(--text); background: var(--subtle-surface); font: inherit; font-weight: 400; outline: none; transition: border-color 0.16s ease, box-shadow 0.16s ease; }
  .model-workbench :is(input, select, textarea):focus { border-color: var(--accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 12%, transparent); }
  .model-workbench select option { color: var(--text); background: var(--panel); }
  .matrix-fieldset { margin: 9px 0; padding: 9px; border: 1px solid var(--border); border-radius: 9px; }
  .matrix-fieldset legend { padding: 0 4px; color: var(--muted); font-weight: 550; }
  .matrix-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }
  .matrix-grid input { min-width: 0; padding: 7px 4px !important; text-align: center; }
  .field-row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .build-button { width: 100%; margin-top: 11px; padding: 8px; border: 1px solid color-mix(in srgb, var(--accent-2) 70%, transparent) !important; border-radius: 9px; color: var(--bg) !important; background: linear-gradient(135deg, var(--accent), var(--accent-2)); font-weight: 700; transition: filter 0.16s ease, transform 0.16s ease; }
  .build-button:hover:not(:disabled) { filter: brightness(1.08); transform: translateY(-1px); }
  .build-button:disabled { opacity: 0.55; }
  .model-error { margin-top: 9px; color: #fca5a5; }
  .model-status { margin-top: 9px; color: #86efac; overflow-wrap: anywhere; }
  .model-note, .model-workbench small { color: var(--muted); }
  .model-note { margin: 11px 0 0; padding-top: 10px; border-top: 1px solid var(--border); font-size: 11px; }
  .secondary-button { width: 100%; padding: 7px 9px; border: 1px solid var(--border) !important; border-radius: 9px; color: var(--text); background: var(--subtle-surface); font: inherit; font-weight: 650; }
  .secondary-button:hover:not(:disabled) { color: var(--accent); border-color: var(--accent) !important; }
  .secondary-button:disabled { opacity: 0.5; }
  .draw-button { display: flex; align-items: center; justify-content: center; gap: 7px; margin-bottom: 8px; }
  .draw-button svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }
  .interface-columns { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .interface-columns section { padding: 9px; border: 1px solid var(--border); border-radius: 10px; background: var(--subtle-surface); min-width: 0; }
  .interface-columns section > strong { color: var(--accent); font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; }
  .advanced-options { margin: 10px 0; padding: 8px 9px; border: 1px solid var(--border); border-radius: 9px; }
  .advanced-options summary { color: var(--muted); cursor: pointer; font-weight: 650; }
  .check-label { display: flex !important; grid-template-columns: none !important; flex-direction: row; align-items: center; gap: 7px !important; }
  .check-label input { width: auto !important; accent-color: var(--accent-2); }
  .interface-candidates { display: grid; gap: 5px; max-height: 180px; margin: 10px 0; overflow-y: auto; }
  .interface-candidates button { display: grid; grid-template-columns: 1fr repeat(4, auto); gap: 9px; align-items: center; width: 100%; padding: 8px; border: 1px solid var(--border) !important; border-radius: 8px; color: var(--muted); background: var(--subtle-surface); text-align: left; font: inherit; }
  .interface-candidates button strong { color: var(--text); }
  .interface-candidates button:hover, .interface-candidates button.selected { border-color: var(--accent) !important; color: var(--accent); background: color-mix(in srgb, var(--accent) 8%, transparent); }
  .sketcher-overlay { position: absolute; z-index: 100000004; inset: 0; display: grid; place-items: center; padding: 14px; background: var(--modal-overlay); }
  .sketcher-dialog { position: relative; display: flex; flex-direction: column; width: min(1000px, 100%); height: min(700px, 100%); overflow: hidden; border: 1px solid var(--border); border-radius: var(--radius); background: var(--panel); box-shadow: var(--popup-shadow); }
  .sketcher-dialog > header { display: flex; align-items: center; justify-content: space-between; padding: 9px 12px; border-bottom: 1px solid var(--border); color: var(--text); font: 13px/1.4 'Manrope', system-ui, sans-serif; }
  .sketcher-host { flex: 1; min-height: 0; background: white; }
  .sketcher-host :global(> div) { width: 100%; height: 100%; }
  .sketcher-loading { position: absolute; inset: 46px 0 48px; display: grid; place-items: center; color: var(--muted); background: var(--panel); font: 12px 'Manrope', system-ui, sans-serif; }
  .sketcher-dialog > footer { display: flex; justify-content: flex-end; gap: 8px; padding: 8px 12px; border-top: 1px solid var(--border); }
  .sketcher-dialog > footer button { width: auto; min-width: 110px; margin: 0; }
  @media (max-width: 620px) {
    .interface-columns, .field-row { grid-template-columns: 1fr; }
    .interface-candidates button { grid-template-columns: 1fr 1fr; }
  }
</style>
