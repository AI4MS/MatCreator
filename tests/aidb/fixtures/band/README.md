# Public atomate2 Si band structure files

Retrieved the historical outputs from atomate2 commit `38294cd74797fdab9a122260f2ce2abbcbb3fc31`:

- [SCF outputs](https://github.com/materialsproject/atomate2/tree/38294cd74797fdab9a122260f2ce2abbcbb3fc31/tests/test_data/vasp/Si_band_structure/static/outputs)
- [Line-mode NSCF outputs](https://github.com/materialsproject/atomate2/tree/38294cd74797fdab9a122260f2ce2abbcbb3fc31/tests/test_data/vasp/Si_band_structure/non-scf_line/outputs)

The upstream `CHGCAR.gz` fixture is empty. It cannot establish charge-density identity or serve as a usable restart input. The replay uses original NSCF eigenvalues and output. Its upstream test injects NSW=0 and IBRION=-1 into the public static XML and uses a synthetic charge grid to check transport and identity. No physical SCF-to-NSCF charge-density continuity is inferred.
