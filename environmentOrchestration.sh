#!/bin/bash

# This script is a first attempt at getting a whole spack setup process up and
# running. The idea is to set up the gcc compiler, the global compiler settings,
# then all of the environments available.
                                                                             
# At time of writing, the first two steps don't work, so they're commented out.
# The loop for environments still needs a starting job id, so there's a simple
# hello world job to start instead. When the compilers.sh script is a real
# thing, and this script can work on a fresh install, that hello world part
# won't be necessary.
                                                                             
# The script doesn't take any arguments, but does assume that there are spack
# environments stored at the TARGET_LOCATION of /shared/home/root/environments,
# and that they conform to the expectations of the `spackSetupEnvironment.sh`
# script, which is expected to initialize a new environment, then install its
# dependencies.
                                                                             
# The script isn't intended to run via sbatch, since it sets up a chain of
# dependent slurm jobs. Instead, just run this script as-is from
# /shared/home/root, and it should set up all of the jobs to configure all of
# the environments articulated in /shared/home/root/environments.

# # Install gcc compiler
# jobid0=$(sbatch -c 8 --parsable spackSinglePackage.sh "gcc@14.1.0")
# echo "Submitted gcc install job, jobid ${jobid0}"

# # Set up compiler settings
# # compilers.sh does not currently exist
# loopjobid=$(sbatch --parsable --dependency="afterok:${jobid0}" compilers.sh)
# echo "Submitted compiler setup job, jobid ${loopjobid}"

# Get an initial job ID with a simple hello world command
loopjobid=$(echo -e '#!/bin/bash\necho "hello world"' | sbatch --parsable)

TARGET_LOCATION="/shared/home/root/environments"

# Check if the target location exists
if [ -d "$TARGET_LOCATION" ]; then
  # Iterate through all the directories in the target location
  for dir in "$TARGET_LOCATION"/*/ ; do
    # Check if it is a directory
    if [ -d "$dir" ]; then
      # Extract the directory name from the path
      dir_name=$(basename "$dir")

      # Print the directory name or use it as needed
      echo "Setting up job for $dir_name"

      nextjobid=$(sbatch --parsable --dependency="afterok:${loopjobid}" spackSetupEnvironment.sh "${dir_name}")
      loopjobid=$nextjobid
      echo "Submitted job to set up ${dir_name} environment, job id ${loopjobid}"
    fi
  done
else
  echo "Target location not found: $TARGET_LOCATION"
fi
