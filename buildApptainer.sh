#!/bin/bash
#SBATCH_OUTPUT=/shared/home/root/scripts/logs/buildApptainer-%j.log
#SBATCH_ERROR=/shared/home/root/scripts/logs/buildApptainer-%j.log

# This script is a helper script for building Apptainer image files as a batch
# job in slurm. The script has one mandatory argument, and another optional arg,
# invoked like so:
                                                                             
# `buildApptainer.sh defFileName (sandbox)`
                                                                             
# The `defFileName` argument is mandatory, and is the name of the apptainer
# definition (.def) file, which will also be the name of the .sif file output by
# the build process.
                                                                             
# The `sandbox` argument is optional, and defines whether or not to create a
# sandbox image. Building a sandbox image takes much longer, and results in a
# folder rather than an image file. In theory, this can be used to execute the
# container with a writable file system, but I haven't had success with that.
# The default is to not build a sandbox image.
                                                                             
# When running this script as a batch process via slurm (recommended in HUIT
# OOD), it can be invoked like so:
                                                                             
# `sbatch -c 8 buildApptainer.sh defFileName (sandbox)`
                                                                             
# This runs the script with 8 CPUs and the arguments provided.
                                                                             
# References: https://apptainer.org/docs/user/main/build_a_container.html
# https://apptainer.org/docs/user/main/definition_files.html

if [ $1 -eq 0 ]
  then
    echo "Apptainer build name is required, e.g. `buildApptainer.sh containerFileName`"
    exit 1
fi

IMAGE_NAME=$1

if [ ! -f /shared/apptainerImages/$IMAGE_NAME.def ]; then
    echo "No definition file at /shared/apptainerImages/$IMAGE_NAME.def"
    exit 1
fi

if [ -f /shared/apptainerImages/$IMAGE_NAME.sif ]; then
    echo "Image file /shared/apptainerImages/$IMAGE_NAME.sif already exists, will be overwritten."
fi

# Default value for the optional argument
DEFAULT_SANDBOX=false

# Assign the first script argument to a variable; use the default if it's not set
SANDBOX=${2:-$DEFAULT_SANDBOX}

. /shared/spack/share/spack/setup-env.sh
spack env activate apptainer

export TMPDIR=/scratch

if $SANDBOX; then
        echo "Building sandbox container"
        apptainer build --sandbox --force /shared/apptainerImages/$IMAGE_NAME /shared/apptainerImages/$IMAGE_NAME.def
else
        echo "Building non-sandbox container"
        apptainer build --force /shared/apptainerImages/$IMAGE_NAME.sif /shared/apptainerImages/$IMAGE_NAME.def
fi
