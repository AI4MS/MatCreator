"""Read the actual VASP input directory at the managed Batch Job boundary."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re

from matcreator.aidb.aimd import MD_TYPE, md_matches
from matcreator.aidb.service import AIDBError
from matcreator.aidb.vasp_archive import file_digest


_CORE_INPUTS = {"INCAR", "POSCAR", "POTCAR"}
_METADATA_FILES = {"SUBMISSION.md", "README.md", "README.txt", "README"}
_REDIRECTIONS = r"(?:\s+\d?>>?\s*(?:[\w.-]+|&\d))*\s*"
_VASP = re.compile(
    r"\s*(?:(?:mpirun|mpiexec)\s+(?:-np|-n)\s+\d+\s+)?(?:/[\w/.-]+/)?"
    r"(vasp_(?:std|gam|ncl))" + _REDIRECTIONS
)
_SHELL_SCRIPT = re.compile(r"\s*(?:bash|sh)\s+(?:\./)?([\w.-]+\.sh)" + _REDIRECTIONS)
_LAUNCH_SETUP = re.compile(
    r"set\s+-[euo]+(?:\s+pipefail)?|source\s+/opt/intel/oneapi/setvars\.sh"
    r"|export\s+FI_PROVIDER=tcp|export\s+OMP_NUM_THREADS=\d+"
)


def has_vasp_inputs(spec) -> bool:
    """Identify prepared VASP inputs even when their launch cannot be verified."""
    if not spec.get("input_root") or not spec.get("input_path"):
        return False
    folder = Path(spec["input_root"]) / spec["input_path"]
    return folder.is_dir() and sum((folder / name).exists() for name in _CORE_INPUTS) >= 2


def vasp_launch(folder: Path, command: str) -> tuple[str, Path | None] | None:
    if match := _VASP.fullmatch(command):
        return match[1], None
    script = _SHELL_SCRIPT.fullmatch(command)
    if not script:
        return None
    path = folder / script[1]
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        return None
    lines = [line.strip() for line in path.read_text().splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    if lines and all(_LAUNCH_SETUP.fullmatch(line) for line in lines[:-1]):
        if match := _VASP.fullmatch(lines[-1]):
            return match[1], path
    return None


@dataclass(frozen=True)
class RelaxationInputs:
    formula: str
    file_hashes: dict[str, str]
    incar: dict
    structure: object
    kpoints: object
    potentials: tuple[str, ...]
    lexch: tuple[str, ...]
    executable: str
    launcher_hash: str | None
    calculation_type: str

    @classmethod
    def from_batch_spec(cls, spec):
        if spec.get("input_root") and spec.get("input_path"):
            folder = Path(spec["input_root"]) / spec["input_path"]
            if folder.resolve() == folder and not folder.is_symlink():
                return cls.read(folder, spec["command"], vasp_type=spec.get("vasp_type"))
        return None

    @classmethod
    def read(cls, folder: Path, command: str, *, vasp_type=None) -> RelaxationInputs | None:
        from pymatgen.io.vasp import Incar, Kpoints, Poscar

        launch = vasp_launch(folder, command) if folder.is_dir() else None
        if launch is None:
            return None
        executable, script = launch
        paths = {path.name: path for path in folder.iterdir()}
        allowed = _CORE_INPUTS | _METADATA_FILES | {"KPOINTS", "WAVECAR", "CHGCAR"}
        if script is not None:
            allowed.add(script.name)
        if paths.keys() - allowed:
            return None
        if not _CORE_INPUTS <= paths.keys() or any(path.is_symlink() or not path.is_file() for path in paths.values()):
            return None
        incar = Incar.from_file(paths["INCAR"])
        if incar.get("ML_LMLFF"):
            return None
        nsw = incar.get("NSW", 0)
        icharg = incar.get("ICHARG", 2)
        if icharg >= 10:
            if icharg != 11 or nsw != 0 or incar.get("IBRION", -1) != -1 or vasp_type is not None:
                raise AIDBError("托管 NSCF 能带计算需要 ICHARG=11、NSW=0 和 IBRION=-1", "invalid_input")
            if not {"KPOINTS", "CHGCAR"} <= paths.keys():
                raise AIDBError("NSCF 缺少实际 KPOINTS 或上游 CHGCAR", "invalid_input")
            if incar.get("ISTART", 0) and "WAVECAR" not in paths:
                raise AIDBError("NSCF 缺少 ISTART 所需的上游 WAVECAR", "invalid_input")
            calculation_type = "nscf"
        elif nsw == 0:
            calculation_type = "matpes" if vasp_type == "matpes" else "static"
        elif vasp_type is None and nsw > 0 and incar.get("IBRION") in (1, 2, 3):
            calculation_type = "relaxation"
        elif vasp_type is None and nsw > 0 and incar.get("IBRION") == 0:
            calculation_type = MD_TYPE
        else:
            return None
        if "KPOINTS" not in paths and not incar.get("KSPACING"):
            return None
        structure = Poscar.from_file(paths["POSCAR"], check_for_potcar=False).structure
        if not structure.is_ordered or not len(structure):
            return None
        kpoints = Kpoints.from_file(paths["KPOINTS"]) if "KPOINTS" in paths else None
        if calculation_type == "nscf":
            from matcreator.aidb.band_structure import band_input_path

            band_input_path(kpoints)
        potentials, lexch = [], []
        with paths["POTCAR"].open(encoding="ascii", errors="replace") as stream:
            for line in stream:
                if match := re.match(r"^\s*TITEL\s*=\s*(.+)", line):
                    potentials.append(match[1].strip())
                if match := re.match(r"^\s*LEXCH\s*=\s*([A-Z]+)", line):
                    lexch.append(match[1])
        if not potentials or len(potentials) != len(lexch):
            return None
        hashes = {name: file_digest(path) for name, path in paths.items()
                  if name not in _METADATA_FILES and path != script}
        return cls(structure.composition.reduced_formula, hashes, dict(incar), structure, kpoints,
                   tuple(potentials), tuple(lexch), executable, file_digest(script) if script else None,
                   calculation_type)

    @property
    def revision(self):
        return {"sha256": self.file_hashes, "executable": self.executable, "launcher_sha256": self.launcher_hash}

    def matches(self, record: dict) -> bool:
        """Require tracked input identity as well as independent output evidence."""
        from monty.json import jsanitize
        import numpy as np
        from pymatgen.core import Structure

        metadata = record.get("source_metadata") or {}
        facts = metadata.get("vasp_facts") or {}
        source = facts.get("source") or {}
        method = facts.get("method") or {}
        parameters = record.get("calculation_parameters") or {}
        submitted = source.get("submitted_inputs") or {}
        labels = facts.get("labels") or {}
        checks = source.get("xml_outcar_checks") or {}
        if (metadata.get("logical_namespace") not in {"fine_tune_working", "bulk_base_pbe", "bulk_dft_u_archive"}
                or metadata.get("software") != "VASP" or method.get("code") != "VASP"
                or record.get("calculation_type") != self.calculation_type
                or (facts.get("sampling") or {}).get("stored_scope") != "final_frame"
                or type(record.get("frame_index")) is not int or record["frame_index"] < 0
                or labels.get("electronic_converged") is not True
                or (self.calculation_type == "relaxation" and labels.get("ionic_converged") is not True)
                or labels.get("energy_unit") != "eV/cell" or labels.get("force_unit") != "eV/Angstrom"
                or not checks or any(value is not True for value in checks.values())
                or submitted.get("provider") not in {"bohr_batchjob", "bohr_sandbox"}
                or not submitted.get("run_id")
                or not source.get("run_fingerprint") or submitted.get("run_fingerprint") != source["run_fingerprint"]
                or submitted.get("executable") != self.executable or submitted.get("sha256") != self.file_hashes
                or tuple(method.get("potcar_symbols") or ()) != self.potentials):
            return False
        for name, item in (source.get("workflow_inputs") or {}).items():
            if name in self.file_hashes and item.get("sha256") != self.file_hashes[name]:
                return False
            if name in {"POSCAR", "KPOINTS"} and item.get("verified") is not True:
                return False
            if name == "INCAR" and item.get("verified") is not True and item.get("unverified_tags") != ["NCORE"]:
                return False

        if self.calculation_type in {"static", "matpes", "nscf"}:
            identity = json.dumps([submitted["provider"], submitted["run_id"]], separators=(",", ":"))
            fingerprint = sha256(identity.encode()).hexdigest()
            if (fingerprint != source["run_fingerprint"]
                    or record.get("source_id") != f'vasp:{fingerprint}:{record["frame_index"]}'
                    or parameters.get("NSW") != 0):
                return False
        initial = Structure.from_dict(facts["initial_structure"]["structure"])
        final = Structure.from_dict(record["structure"])
        if (initial.species != self.structure.species or final.species != initial.species
                or not np.allclose(initial.lattice.matrix, self.structure.lattice.matrix, rtol=0, atol=1e-7)
                or not np.allclose(initial.frac_coords, self.structure.frac_coords, rtol=0, atol=1e-7)):
            return False
        if self.calculation_type in {"static", "matpes", "nscf"}:
            displacement = final.frac_coords - initial.frac_coords
            displacement -= np.rint(displacement)
            if (not np.allclose(initial.lattice.matrix, final.lattice.matrix, rtol=0, atol=1e-7)
                    or not np.allclose(displacement, 0, rtol=0, atol=1e-7)):
                return False

        spin = {"ISPIN": self.incar.get("ISPIN", 1), "LSORBIT": self.incar.get("LSORBIT", False),
                "LNONCOLLINEAR": self.incar.get("LNONCOLLINEAR", self.incar.get("LSORBIT", False))}
        if any(method.get("spin", {}).get(key) != value or parameters.get(key) != value
               for key, value in spin.items()):
            return False
        u_active = self.incar.get("LDAU", False)
        if parameters.get("LDAU") is not u_active or bool(record.get("hubbard_u")) != u_active:
            return False
        if u_active:
            for key in ("LDAUL", "LDAUU", "LDAUJ", "LDAUTYPE"):
                if key in self.incar and parameters.get(key) != self.incar[key]:
                    return False
        functional_input = {key: self.incar[key] for key in ("GGA", "METAGGA", "XC") if key in self.incar}
        runtime = method.get("functional_runtime") or {}
        if (method.get("functional_input") != functional_input
                or any(key not in runtime or runtime[key] != parameters.get(key)
                       for key in ("GGA", "LHFCALC", "LMETAGGA"))):
            return False
        # The supported positive path has an explicit GGA or the potential's
        # verified LEXCH default. Unknown functional aliases and hybrid/meta
        # variants still reach the normal submit path.
        if (self.incar.get("LHFCALC", False) or runtime["LHFCALC"] is not False
                or self.incar.get("METAGGA") or runtime["LMETAGGA"] is not False or functional_input.get("XC")):
            return False
        known = {"PE": "PBE", "PS": "PBEsol", "91": "PW91"}
        supplied_gga = functional_input.get("GGA")
        if supplied_gga:
            expected = known.get(str(supplied_gga).upper())
            if str(runtime["GGA"]).upper() != str(supplied_gga).upper():
                return False
        else:
            expected = "PBE" if set(self.lexch) == {"PE"} and runtime["GGA"] in {"--", "PE"} else None
        stored = {expected, supplied_gga} - {None}
        if u_active:
            stored = {value + "+U" for value in stored}
        if expected is None or record.get("functional") not in stored:
            return False
        cutoff = self.incar.get("ENCUT")
        if (cutoff is None or method.get("input_encut_ev") != cutoff
                or parameters.get("ENCUT", parameters.get("ENMAX")) != cutoff):
            return False
        if any(parameters.get(key) != self.incar[key] for key in ("IBRION", "NSW", "ISIF") if key in self.incar):
            return False
        if parameters.get("ISTART") != self.incar.get("ISTART", 0) or parameters.get("ICHARG") != self.incar.get("ICHARG", 2):
            return False
        if parameters["ISTART"] and "WAVECAR" not in self.file_hashes:
            return False
        if parameters["ICHARG"] == 1 and "CHGCAR" not in self.file_hashes:
            return False
        if self.calculation_type == MD_TYPE:
            if (submitted.get("launcher_sha256") != self.launcher_hash
                    or not md_matches(self.incar, parameters, facts["sampling"], record["frame_index"])):
                return False
        if self.kpoints is None:
            return (self.calculation_type != "relaxation"
                    and self.incar.get("KSPACING") == parameters.get("KSPACING")
                    and self.incar.get("KGAMMA", True) == parameters.get("KGAMMA")
                    and (method.get("kpoints") or {}).get("nkpoints") == 0)
        if self.calculation_type == "nscf":
            band = facts.get("band_structure") or {}
            return (parameters.get("ICHARG") == 11 and (facts.get("sampling") or {}).get("type") == "nscf"
                    and bool(band.get("nkpoints")) and bool(band.get("nbands")))
        current_mesh, old_mesh = jsanitize(self.kpoints.as_dict()), method.get("kpoints") or {}
        return current_mesh.get("nkpoints") == old_mesh.get("nkpoints") == 0 and all(
            current_mesh.get(key) == old_mesh.get(key) for key in ("generation_style", "kpoints", "usershift")
        )
