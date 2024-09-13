#!/bin/bash

# This script follows the setup process outlined at
# https://osc.github.io/ood-documentation/latest/how-tos/app-development/enabling-development-mode.html
# to set up app development folders for a given user. All that you need to
# provide is a username (net ID). The script will check if they have a home
# directory and won't run if they don't, so this will only work for users who
# have already logged into the system at least once.

# The script will only work when run on the portal node, as the symlink it
# creates needs to exist within the OOD install on the portal node.

if [ -z "$1" ]
  then
    echo "User name is required, e.g. '$0 username'"
    exit 1
  else
    if [ -d "/shared/home/$1" ]
      then
        echo "Creating app dev folder for user '$1'"
      else
        echo "User '$1' does not have a home directory, so app dev setup cannot continue."
        exit 2
    fi
fi

# Create dev folder in user dir
mkdir -p "/shared/home/$1/ondemand/dev"

# Get user's group and fix permissions
TARGET_GROUP=$(id -gn $1)
chown -R $1:$TARGET_GROUP /shared/home/$1/ondemand

# Create dev folder for user in OOD install
mkdir -p "/var/www/ood/apps/dev/$1"

# Link user's dev folder to OOD dev folder
ln -s "/shared/home/$1/ondemand/dev" "/var/www/ood/apps/dev/$1/gateway"

echo "Done creating app dev folder setup"
