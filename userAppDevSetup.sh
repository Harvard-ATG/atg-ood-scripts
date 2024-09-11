#!/bin/bash

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
chown $1:$TARGET_GROUP /shared/home/$1/ondemand/dev

# Create dev folder for user in OOD install
mkdir -p "/var/www/ood/apps/dev/$1"

# Link user's dev folder to OOD dev folder
ln -s "/shared/home/$1/ondemand/dev" "/var/www/ood/apps/dev/$1/gateway"

echo "Done creating app dev folder setup"