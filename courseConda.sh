#!/bin/bash
#SBATCH --output /shared/home/root/scripts/logs/courseConda-%j.log
#SBATCH --error /shared/home/root/scripts/logs/courseConda-%j.log

# DESCRIPTION:
#
# This script is used to create a conda environment using a spack environment, 
# but in its own folder, rather than as a named environment within the spack 
# environment.
#
# It assumes that miniconda or an equivalent "conda" binary is already
# available in the environment and also that the conda environment name and
# dependencies are specified in a YAML file.
#
# It is intended for creating conda environments managed by course staff within 
# course shared folders, but is set up to install conda to arbitrary paths as 
# needed.
#
# USAGE:
#
# sbatch -c 8 courseConda.sh [spack-env] [path/to/environment.yml] [/path/to/install/conda/environment]
# sbatch -c 8 courseConda.sh conda /shared/home/root/environments/cs1090b/conda.yml /shared/courseSharedFolders/142601/142601/cs109b
#
# SEE ALSO:
#
# https://conda.io/projects/conda/en/latest/user-guide/tasks/manage-environments.html
#

if [[ -z "$1" ]]; then
    echo "Spack environment name is required, e.g. $0 [spack-env] [/path/to/environment.yml] [/path/to/install/conda/environment]"
    exit 1
fi

if [[ -z "$2" ]]; then
    echo "Conda environment file is required, e.g. $0 [spack-env] [/path/to/environment.yml] [/path/to/install/conda/environment]"
    exit 1
fi

if [[ -z "$3" ]]; then
    echo "Conda environment location is required, e.g. $0 [spack-env] [/path/to/environment.yml] [/path/to/install/conda/environment]"
    exit 1
fi

SPACK_ENVIRONMENT=$1
CONDA_ENVIRONMENT_FILE=$2
CONDA_ENVIRONMENT_LOCATION=$3

. /shared/spack/share/spack/setup-env.sh
echo "Activating spack environment: $SPACK_ENVIRONMENT"
spack env activate $SPACK_ENVIRONMENT
echo "Creating conda environment from file: $CONDA_ENVIRONMENT_FILE"
echo "Environment will be created at $CONDA_ENVIRONMENT_LOCATION"
echo ""
cat $CONDA_ENVIRONMENT_FILE
echo ""
conda env create --file $CONDA_ENVIRONMENT_FILE --prefix $CONDA_ENVIRONMENT_LOCATION
echo "Done!"
