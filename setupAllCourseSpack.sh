#!/bin/bash
#SBATCH --output /shared/home/root/scripts/logs/setupAllCourseSpack-%j.log
#SBATCH --error /shared/home/root/scripts/logs/setupAllCourseSpack-%j.log

# This script will set up course spack installations with the global spack
# installation referenced as an upstream. It invokes the `setup_spack.sh` script
# for any Canvas course group that it pulls from LDAP that has a course folder,
# but does not already have a spack installation. The `setup_spack.sh` script is
# created when the course folder is created by the `makeCourseFolder.sh` script
# in the HUIT OOD IAC repo.

GLOBAL_SPACK_ROOT=/shared/spack
SHARED_FOLDER_BASE="/shared/courseSharedFolders"

LDAP_URI=$(sed -nr 's/ldap_uri = (.*)/\1/p' /etc/sssd/sssd.conf)
LDAP_KEY=$(sed -nr 's/ldap_default_authtok = (.*)/\1/p' /etc/sssd/sssd.conf)

CANVAS_COURSE_IDS=$(ldapsearch -H $LDAP_URI -x -D "uid=OnDemand,ou=ldap-apps,dc=harvard,dc=edu" -w $LDAP_KEY -b "ou=groups,dc=harvard,dc=edu" "(cn=canvas*)" cn | sed -nr 's/^cn: (.*)/\1/p' | sed -nr "s/^canvas([0-9]+)-.+/\1/p" | sort | uniq)

for CANVAS_COURSE_ID in $CANVAS_COURSE_IDS; do
    course_folder=$SHARED_FOLDER_BASE/${CANVAS_COURSE_ID}outer/$CANVAS_COURSE_ID
    if [[ ! -d "$course_folder" ]]; then
        echo "[$(date -Iseconds)] [$CANVAS_COURSE_ID] Skipping spack setup because course folder does not exist: $course_folder"
        continue
    fi

    course_spack_root="$course_folder/spack"
    if [[ -d "$course_spack_root" ]]; then
        echo "[$(date -Iseconds)] [$CANVAS_COURSE_ID] Spack installation already exists: $course_spack_root"
    else
        if [[ -e "$course_folder/setup_spack.sh" ]]; then
            echo "[$(date -Iseconds)] [$CANVAS_COURSE_ID] Starting spack setup"
            $course_folder/setup_spack.sh
            echo "[$(date -Iseconds)] [$CANVAS_COURSE_ID] Completed spack setup"
        else
            echo "[$(date -Iseconds)] [$CANVAS_COURSE_ID] Spack setup script is missing: $course_folder/setup_spack.sh"
        fi

    fi
done
