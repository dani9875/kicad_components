#!/bin/bash

# Define source and destination directories
SOURCE_DIR="./original_components"
DEST_DIR="./3D"

# Create destination directory if it does not exist
mkdir -p "$DEST_DIR"

# Find and copy .step files
find "$SOURCE_DIR" -type f -name "*.step" -exec cp {} "$DEST_DIR" \;

# Find and copy .wrl files
find "$SOURCE_DIR" -type f -name "*.wrl" -exec cp {} "$DEST_DIR" \;

echo "All .step and .wrl files have been copied to the $DEST_DIR directory."

