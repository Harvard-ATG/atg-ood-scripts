#!/bin/bash

. /shared/spack/share/spack/setup-env.sh

# This line is a point of failure if multiple versions of either gsl or
# openblas are installed for some reason.
spack load gsl openblas

# The paths for the dependencies must be added to PATH environment variables so
# that the compiler can properly find them. It's not clear why this doesn't
# happen by default in Spack, but doing it this way allows the software to be
# set up. If this isn't done, the build process complains about missing
# libraries and/or files when it tries to compile Eigensoft.
DEPENDENCIES=("gsl" "openblas")
for dep in "${DEPENDENCIES[@]}"; do
    locationLine=($(spack find -p "$dep" | grep "$dep"))
    depLocation="${locationLine[1]}"
    libLocation="${depLocation}/lib"
    incLocation="${depLocation}/include"
    export LIBRARY_PATH="$libLocation:$LIBRARY_PATH"
    export C_INCLUDE_PATH="$incLocation:$C_INCLUDE_PATH"
done

git clone https://github.com/DReichLab/EIG.git

cd EIG/src

make
make install
