#!/bin/bash
#SBATCH --output /shared/home/root/scripts/logs/spackSinglePackage-%j.log
#SBATCH --error /shared/home/root/scripts/logs/spackSinglePackage-%j.log

# Installing some spack packages can be time consuming, so this script was
# created to make it easier to install a package with an asynchronous batch job
# via slurm. The script just takes whatever package name it was provided and
# installs it via spack, after ensuring that the global spack installation is
# active. This script only installs a package

# Running this script outside of a batch context looks like
# `spackSinglePackage.sh packageName`, where packageName is the name (or spec,
# including version number, compiler version, and compiler flags) for a package
# to be installed.

# Running the script via sbatch is similar, but looks like this: `sbatch -c 8
# spackSinglePackage.sh "packageName"`. In that case, 8 CPU cores are allocated
# to the installation job to give it some more resources to try to get it to
# install faster.

if [ $1 -eq 0 ]
  then
    echo "Package name is required, e.g. `spackSinglePackage.sh packageName`"
    exit 1
fi

. /shared/spack/share/spack/setup-env.sh
PACKAGE=$1
echo "Installing $PACKAGE..."
spack install $PACKAGE
echo "Done!"
