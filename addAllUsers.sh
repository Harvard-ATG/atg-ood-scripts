#!/bin/bash

# This script is intended to create user accounts from a text file containing the 
# output of an LDAP query for current HUIT OOD users, in full or in part. For 
# example, this ldap search query will return all canvas user groups in the LDAP server:
# ldapsearch -H ldaps://ldap-shadow.iam.harvard.edu -x -D "uid=OnDemand,ou=ldap-apps,dc=harvard,dc=edu" -W -b "ou=grouper,ou=groups,dc=harvard,dc=edu" "(cn=canvas*)" cn uniqueMember
# This script was created by the Mistral Large 2407 LLM and modified for use in this context.

# Define the file to read from
input_file=$1

# Define the regular expression pattern
regex_pattern="uid=([a-z]+[0-9]+)"

# Define the existing script to call
existing_script="/etc/ood/add_user.sh"

# Check if the input file exists
if [ ! -f "$input_file" ]; then
  echo "Input file $input_file does not exist."
  exit 1
fi

# Check if the existing script is executable
if [ ! -x "$existing_script" ]; then
  echo "Existing script $existing_script is not executable."
  exit 1
fi

# Log commands to stdout
set -x

# Read the file and find matching values
while IFS= read -r line; do
  if [[ $line =~ $regex_pattern ]]; then
    # Extract the matched value
    matched_value="${BASH_REMATCH[1]}"
    # Call the existing script with the matched value
    $existing_script "$matched_value"
    echo "sleeping for 2"
    sleep 2
  fi
done < "$input_file"
