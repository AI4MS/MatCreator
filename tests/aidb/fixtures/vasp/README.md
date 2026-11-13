# AIDB VASP fixtures

These files are inputs to the offline parser, archive and managed-job replay tests.

- `si-pbe-relax/` contains INCAR, KPOINTS, OUTCAR and vasprun.xml from the same completed local Si relaxation used for the AIDB acceptance. The source XML SHA-256 is `968f94c022d57c6da8026b49c2f710a2e92011cfc3519cf8e1b74479a2eec394`. The archive and job replay tests compare hashes and scientific values, so keep the source bytes unchanged.
- `nacl-static/`, `si-md-1/` and `si-r2scan-relax/` come from the atomate2 [NaCl static](https://github.com/materialsproject/atomate2/tree/38294cd74797fdab9a122260f2ce2abbcbb3fc31/tests/test_data/vasp/NaCl_phonons/static/outputs), [Si multi-stage MD](https://github.com/materialsproject/atomate2/tree/38294cd74797fdab9a122260f2ce2abbcbb3fc31/tests/test_data/vasp/Si_multi_md) and [Si r2SCAN relaxation](https://github.com/materialsproject/atomate2/tree/38294cd74797fdab9a122260f2ce2abbcbb3fc31/tests/test_data/vasp/Si_mp_meta_gga_relax/r2scan_relax/outputs) examples at commit `38294cd`. Their [upstream license](LICENSE.atomate2) is included.
