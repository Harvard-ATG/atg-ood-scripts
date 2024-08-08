#!/bin/bash
#SBATCH --output /shared/home/root/scripts/logs/spackSetupEnvironment-%j.log
#SBATCH --error /shared/home/root/scripts/logs/spackSetupEnvironment-%j.log

# This script is used to set up a spack environment that has not yet been
# created, but has a definition file at
# /shared/home/root/environments/<environmentName>/spack.yml. The environment
# will be created with the name of the folder above the spack.yml definition,
# and this script will not only define the environment, but also install the
# packages that it requires. 

# If the environment has already been created, this script will fail, since it
# assumes the environment does not already exist. If you just want to install
# packages for an existing environment, use `spackEnvironment.sh`.

if [ $1 -eq 0 ]
  then
    echo "Environment name is required, e.g. `spackSetupEnvironment.sh environmentName`"
    exit 1
fi

. /shared/spack/share/spack/setup-env.sh
ENVIRONMENT=$1
echo "Creating environment $ENVIRONMENT..."
spack env create $ENVIRONMENT "/shared/home/root/environments/$ENVIRONMENT/spack.yml"
echo "Activating environment $ENVIRONMENT..."
spack env activate $ENVIRONMENT
echo "Installing packages for $ENVIRONMENT..."
spack install
echo "Done!"
