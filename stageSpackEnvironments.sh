#!/bin/bash
#SBATCH_OUTPUT=/shared/home/root/scripts/logs/stageSpackEnvironments-%j.log
#SBATCH_ERROR=/shared/home/root/scripts/logs/stageSpackEnvironments-%j.log

# This script iterates through installed OOD apps and looks for a folder in each
# app called "spack-environment". If this folder is found, it copies its
# contents into the target environment directory. The "spack-environment" folder
# is expected to contain 1 or more directories, each containing a "spack.yml"
# file defining the environment named in its parent folder. This script can set
# the stage for the `environmentOrchestration.sh` script when run on the portal
# node. The actual orchestration script needs to be run on the head node or a
# compute node, so that the packages in the environments get configured
# correctly.

# Set OOD apps directory
appdir="/var/www/ood/apps/sys/"
envdir="/shared/home/root/environments"

# Ensure target directory exists
mkir -p /shared/home/root/environments

# iterate through apps
for folder in "$appdir"/*; do
  if [ -d "$folder" ]; then
    # check if 'spack-environment' subfolder exists
    if [ -d "$folder/spack-environment" ]; then
      # set up environment
      cp -r "$folder/spack-environment/*" "/shared/home/root/environments"
    fi
  fi
done
