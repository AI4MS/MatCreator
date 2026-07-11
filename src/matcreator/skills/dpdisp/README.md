If you use dpdispatcher to submit jobs to Bohrium, configure credentials and compute defaults in `~/.matcreator/config.yaml` (or the server control-plane `config.yaml` for deployment defaults):
```yaml
bohrium:
	email: your_email
	password: your_password
	project_id: "1111"

compute:
	vasp_image: VASP_IMAGE
	vasp_machine: c16_m32_cpu
	deepmd_model_path: default_model_path
	deepmd_image: deepmd_image
	deepmd_machine: 1 * NVIDIA V100_32g

# Below are some environment variables for debugging and development, you can ignore them if you don't know what they are for.
env:
	INFO_DB_PATH: PATH_TO_INFO.db
	BOHRIUM_DEEPMD_ASE_IMAGE: deepmd_image_with_ase
	BOHRIUM_DEEPMD_ASE_MACHINE: 1 * NVIDIA V100_32g
```