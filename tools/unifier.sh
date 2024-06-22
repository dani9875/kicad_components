#!/bin/bash

# Define the root directory
ROOT_DIR="original_components"

# Loop through each subdirectory in the root directory
for SUBDIR in "$ROOT_DIR"/*; do
  if [ -d "$SUBDIR" ]; then
    SUBDIR_NAME=$(basename "$SUBDIR")

    # Loop through each file in the subdirectory
    for FILE in "$SUBDIR"/*; do
      if [ -f "$FILE" ]; then
        EXTENSION="${FILE##*.}"
        NEW_NAME="$SUBDIR/$SUBDIR_NAME.$EXTENSION"

        # Rename the file
        mv "$FILE" "$NEW_NAME"
      fi
    done
  fi
done

echo "Files have been renamed."

