#!/bin/bash

# Base directory to search in
BASE_DIR="/var/www/ood/apps/sys"

# Check if base directory exists
if [ ! -d "$BASE_DIR" ]; then
    echo "Error: Directory $BASE_DIR does not exist"
    exit 1
fi

# Change to the base directory
cd "$BASE_DIR" || exit 1

echo "Searching for directories starting with 'ood' in $BASE_DIR"
echo "================================================"

# Find and process directories starting with "ood"
for dir in ood*/; do
    # Check if the glob matched anything and if it's actually a directory
    if [ -d "$dir" ]; then
        echo "Processing: $dir"

        # Change into the directory
        cd "$dir" || { echo "Failed to enter $dir"; continue; }

        # Check if it's a git repository
        if [ -d ".git" ]; then
            echo "  Running git pull origin main..."
            git pull origin main
            if [ $? -eq 0 ]; then
                echo "  ✓ Successfully updated $dir"
            else
                echo "  ✗ Failed to update $dir"
            fi
        else
            echo "  ⚠ Not a git repository, skipping..."
        fi

        # Change back to base directory
        cd "$BASE_DIR" || exit 1
        echo ""
    fi
done
