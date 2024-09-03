#!/bin/bash
#SBATCH --output /shared/home/root/scripts/logs/condaEnvironment-%j.log
#SBATCH --error /shared/home/root/scripts/logs/condaEnvironment-%j.log

# DESCRIPTION:
#
# This script is used to create a conda environment within a spack environment.
# It It assumes that miniconda or an equivalent "conda" binary is already
# available in the environment and also that the conda environment name and
# dependencies are specified in a YAML file. 
#
# USAGE:
#
# sbatch -c 8 condaEnvironment.sh [spack-env] [path/to/environment.yml]
# sbatch -c 8 condaEnvironment.sh conda conda-environment/jupyter/environment.yml
# sbatch -c 8 condaEnvironment.sh cs109a conda-environment/cs109a/environment.yml
#
# SEE ALSO:
#
# https://conda.io/projects/conda/en/latest/user-guide/tasks/manage-environments.html
#

if [[ -z "$1" ]]; then
    echo "Spack environment name is required, e.g. $0 [spack-env] [/path/to/environment.yml]"
    exit 1
fi
if [[ -z "$2" ]]; then
    echo "Conda environment file is required, e.g. $0 [spack-env] [/path/to/environment.yml]"
    exit 1
fi

SPACK_ENVIRONMENT=$1
CONDA_ENVIRONMENT_FILE=$2

. /shared/spack/share/spack/setup-env.sh
echo "Activating spack environment: $SPACK_ENVIRONMENT"
spack env activate $SPACK_ENVIRONMENT
echo "Creating conda environment file: $CONDA_ENVIRONMENT_FILE"
echo ""
cat $CONDA_ENVIRONMENT_FILE
echo ""
conda env create --file $CONDA_ENVIRONMENT_FILE
echo "Done!"
