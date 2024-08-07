# HUIT OOD helper scripts

This repository contains scripts to help run Harvard University IT's Open OnDemand platform (HUIT OOD). These scripts are set up to help with administering the software behind our OnDemand apps, which mostly rely on [Spack](https://spack.io/) packages and environments, as well as [Apptainer](https://apptainer.org/) containers.

Many of the tasks involved in setting up this software benefits from running in the existing batch compute infrastructure of HUIT OOD, which uses [Slurm](https://slurm.schedmd.com/) to manage compute jobs.

## Further Reading

For some context on what these scripts do, and the context in which you might run them, here's some helpful documentation links:

### Slurm
- [`sbatch` command reference]() - How to prepare scripts to run on compute nodes via Slurm

### Spack
- [Spack basic usage docs](https://spack.readthedocs.io/en/latest/basic_usage.html)
- [Spack environments tutorial](https://spack-tutorial.readthedocs.io/en/latest/tutorial_environments.html)

### Apptainer
- [Build an Apptainer container](https://apptainer.org/docs/user/main/build_a_container.html)
- [Apptainer definition file reference](https://apptainer.org/docs/user/main/definition_files.html)
- [Apptainer build command reference](https://apptainer.org/docs/user/main/cli/apptainer_build.html)

### HUIT OOD app repositories
- [Remote Desktop - Basic](https://github.com/Harvard-ATG/ood-remote-desktop)
- [Remote Desktop - ROS/Gazebo + MATLAB](https://github.com/Harvard-ATG/ood-remote-desktop-rosgazebo)
- [Jupyter Lab (Apptainer implementation)](https://github.com/Harvard-ATG/ood-jupyterlab-apptainer)
- [Jupyter Lab (Spack implementation)](https://github.com/Harvard-ATG/ood-jupyterlab-spack)
- [Code Server (VS Code)](https://github.com/Harvard-ATG/bc_osc_codeserver)
