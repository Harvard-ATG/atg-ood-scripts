#!/bin/bash

# The purpose of this script is to check for missing `.ssh` directories in user 
# home directories. If this directory is not present, a user will have issues 
# accessing the OOD terminal and launching interactive apps. If a user is 
# missing their `.ssh` directory, the directory should be backed up, then 
# removed. Once the home directory is removed, running the 
# `/etc/ood/add_user.sh` from the portal node will re-create the home directory 
# with the `.ssh` directory in place. If it was not empty, restore the user's 
# home directory to a new folder in the newly created home directory.

# Base directory where user home directories are located
HOME_DIR_BASE="/shared/home"

# Iterate over each user directory in the home base directory
for user_dir in "$HOME_DIR_BASE"/*; do
    # Check if it's a directory
    if [ -d "$user_dir" ]; then
        # User name extracted from the directory path
        user=$(basename "$user_dir")

        # Full path to the .ssh directory
        ssh_dir="$user_dir/.ssh"

        # Check if .ssh directory exists
        if [ ! -d "$ssh_dir" ]; then
            echo "User '$user' is missing their .ssh directory."
        fi
    fi
done
