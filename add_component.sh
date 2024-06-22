#!/bin/bash

# Check if the correct number of arguments is provided
if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <LCSC_component_number> <new_name>"
    exit 1
fi

LCSC_COMPONENT_NUMBER=$1
NEW_NAME=$2
OUTPUT_DIR=$(pwd)

# Check if easyeda2kicad is installed
if ! pip show easyeda2kicad &> /dev/null; then
    echo "easyeda2kicad is not installed. Installing..."
    pip install easyeda2kicad
    if [ $? -eq 0 ]; then
        echo "easyeda2kicad installed successfully."
    else
        echo "Failed to install easyeda2kicad."
        exit 1
    fi
else
    echo "easyeda2kicad is already installed."
fi

# Convert the component using easyeda2kicad and rename artifacts
easyeda2kicad --symbol --lcsc_id="$LCSC_COMPONENT_NUMBER" --output "$OUTPUT_DIR/$NEW_NAME"
if [ $? -eq 0 ]; then
    echo "Component conversion successful. Artifacts renamed to $NEW_NAME in $OUTPUT_DIR."
else
    echo "Component conversion failed."
    exit 1
fi