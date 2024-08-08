#!/bin/bash
#SBATCH --output /shared/home/root/scripts/logs/spackEnvironment-%j.log
#SBATCH --error /shared/home/root/scripts/logs/spackEnvironment-%j.log

# This script is used for installing all of the dependencies in a spack
# environment. If an environment is created interactively by a series of `spack
# add` commands, the packages articulated are not automatically installed. Since
# this installation can take a while, this script helps with running the install
# as a batch job via slurm.

# Running the script looks like `spackEnvironment.sh environmentName`, where
# `environmentName` is the name of the environment to install dependencies for.

# Running the script as a batch job isn't much different, and looks like this:
# `sbatch -c 8 spackEnvironment.sh environmentName` In this case, the sbatch
# command is running on 8 CPU cores, but different resources can be allocated
# with regular sbatch command options.

if [ $1 -eq 0 ]
  then
    echo "Environment name is required, e.g. `spackEnvironment.sh environmentName`"
    exit 1
fi

. /shared/spack/share/spack/setup-env.sh
ENVIRONMENT=$1
echo "Activating environment $ENVIRONMENT..."
spack env activate $ENVIRONMENT
echo "Installing packages for $ENVIRONMENT..."
spack install
echo "Done!"
