"""Only executed by the configured PyWPEM interpreter; never imported by OAW."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
try:
    from .engine import engine_root
except ImportError:
    from engine import engine_root

def main():
    root, run = map(Path, sys.argv[1:])
    options = json.loads((run / "input.json").read_text(encoding="utf-8"))
    os.environ["MPLBACKEND"]="Agg"
    os.environ["OMP_NUM_THREADS"]="1"
    if options.get("mode") == "match" and options.get("workflow_stage", "search") in {"preopt", "fit"}:
        from refinement import run_pipeline
        run_pipeline(root, run, options)
        return
    if options.get("mode") == "match":
        from matching import match_patterns
        inputs = json.loads((run / "input-snapshot.json").read_text(encoding="utf-8"))
        result = match_patterns(inputs, options, run_dir=run)
        (run / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    sys.path.insert(0,str(engine_root().parent))
    os.chdir(run)
    import numpy as np
    import pandas as pd
    from OAW_XRDfit.src import WPEM
    from OAW_XRDfit.src.EMBraggOpt.EMBraggSolver import WPEMsolver
    np.random.seed(0)
    import random
    random.seed(0)
    if options["demo"]:
        source=engine_root() / "demo"
        shutil.copy2(source / "intensity.csv",run / "intensity.csv")
        shutil.copy2(source / "Mn2O3.cif",run / "sample.cif")
    else:
        (run / "intensity.csv").write_text(options["intensity_csv"],encoding="utf-8")
        (run / "sample.cif").write_text(options["cif"],encoding="utf-8")
    data=pd.read_csv("intensity.csv",header=None)
    if data.shape[1]!=2 or len(data)<20 or not np.isfinite(data.values).all() or not np.all(np.diff(data[0])>0) or not np.all(data[1]>=0):
        raise ValueError("Expected ascending 2theta,intensity CSV, finite nonnegative intensities and >=20 points")
    result={"mode":"fit", "inputs":{name:hashlib.sha256((run/name).read_bytes()).hexdigest() for name in ("intensity.csv","sample.cif")},"converged":False}
    original=WPEMsolver.cal_output_result
    def capture(self):
        values=original(self)
        result["solver"]=dict(zip(["Rp_percent","Rwp_percent","iteration","stop_flag","lattice"],values))
        result["converged"]=int(values[3])==1
        return values
    WPEMsolver.cal_output_result=capture
    var=WPEM.BackgroundFit(data,lowAngleRange=17,poly_n=13,bac_split=16,bac_num=300)
    if options["preoptimize"]:
        WPEM.StructureSolve(no_bac_intensity_file="ConvertedDocuments/no_bac_intensity.csv",cif_file="sample.cif",wavelength=options["wavelength"])
    lattice,_,_=WPEM.CIFpreprocess(filepath="sample.cif",wavelength=options["wavelength"],two_theta_range=(float(data[0].min()),float(data[0].max())))
    shutil.copy2("output_xrd/sampleHKL.csv","peak0.csv")
    WPEM.XRDfit(wavelength=[options["wavelength"]],Var=var,Lattice_constants=[lattice],
        no_bac_intensity_file="ConvertedDocuments/no_bac_intensity.csv",original_file="intensity.csv",bacground_file="ConvertedDocuments/bac.csv",
        bta=.85,asy_C=0,cpu=2,subset_number=11,low_bound=options["low_angle"],up_bound=options["high_angle"],iter_max=options["iterations"],InitializationEpoch=0)
    result["status"]="completed"
    result["interpretation"]="Whole-pattern fit against a supplied candidate CIF; completion does not establish phase identity or full Rietveld refinement."
    result["files"]=[str(p.relative_to(run)) for p in run.rglob("*") if p.is_file() and p.name not in {"input.json","console.log"}]
    (run/"result.json").write_text(json.dumps(result,indent=2,default=lambda v:v.tolist() if hasattr(v,"tolist") else str(v)),encoding="utf-8")

if __name__=="__main__":
    main()
