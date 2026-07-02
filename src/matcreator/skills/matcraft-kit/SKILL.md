---
name: matcraft-kit
description: A modular Python framework for building and analyzing atomic structures with CLI tools for materials modelling.
when_to_use: Use when you need to build, modify, inspect, or analyze atomic structures.
entry: mckit -h
---

# MatCraft Toolkit (`mckit`)

Use this skill to streamline and automate materials modeling workflows. The framework is divided into two primary command categories:

### Operations (Build & Modify)

Tasks dedicated to creating and manipulating atomic structures.

* **Command:** `mckit operate -h`
* **Included tools:** bulk, supercell, surface, interface, adsorption, defect creation, perturbation, molecule creation, nano crystal, solvation, and vdW stacking.
* **Use for:** building structures, generating slabs/interfaces, adding defects, making molecules or clusters, and other geometry edits.

### Observations (Inspect & Analyze)

Tasks dedicated to analyzing structural properties and verifying geometry.

* **Command:** `mckit observe -h`
* **Included tools:** `inspect` for structure summaries and `basic_check` for geometric sanity checks.
* **Inspect output:** basic structure data, detected molecules, vacuum layers, and slab composition information.

---

> **Critical Workflow Rule:** Always check the structure after any structural modification using:
> `mckit observe inspect [<structure_file>]`
> *Never rely solely on intuition—always verify structural integrity programmatically.*
