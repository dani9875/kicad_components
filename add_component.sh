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

# Define the destination directory
DEST_DIR="$OUTPUT_DIR/original_components/$NEW_NAME"
mkdir -p "$DEST_DIR"

# Convert the component using easyeda2kicad and rename artifacts
easyeda2kicad --full --lcsc_id="$LCSC_COMPONENT_NUMBER" --output "$OUTPUT_DIR/"
if [ $? -eq 0 ]; then
    echo "Component conversion successful."
else
    echo "Component conversion failed."
    exit 1
fi 

# Move the .kicad_sym file with no name to the destination directory and rename it
if [ -f "$OUTPUT_DIR/.kicad_sym" ]; then
    mv "$OUTPUT_DIR/.kicad_sym" "$DEST_DIR/$NEW_NAME.kicad_sym"
fi

# Move the contents of .3dshapes directory to the destination directory
if [ -d "$OUTPUT_DIR/.3dshapes" ]; then
    for file in "$OUTPUT_DIR/.3dshapes"/*; do
        mv "$file" "$DEST_DIR/"
    done
fi

# Move the contents of .pretty directory to the destination directory and rename them
if [ -d "$OUTPUT_DIR/.pretty" ]; then
    for file in "$OUTPUT_DIR/.pretty"/*; do
        mv "$file" "$DEST_DIR/"
    done
    mv "$DEST_DIR/.pretty" "$DEST_DIR/$NEW_NAME.pretty"
fi

# Clean up the remaining directories
rm -rf "$OUTPUT_DIR/.3dshapes"
rm -rf "$OUTPUT_DIR/.pretty"

source "tools/unifier.sh"

# Copy the .kicad_sym file to pepy_sym_lib.pretty
NEW_NAME=$2
cp "$DEST_DIR/$NEW_NAME.kicad_mod" "pepy_sym_lib.pretty/"

echo "Files moved to $DEST_DIR and cleanup done."
COUNTER=0

# Add the new component's kicad_sym file to the pepy_sym_lib.kicad_sym file
if [ -f "$DEST_DIR/$NEW_NAME.kicad_sym" ]; then
    echo "Adding $NEW_NAME.kicad_sym to pepy_sym_lib.kicad_sym"

    # Read the new kicad_sym file and update symbol names and footprint properties
    new_content=$(awk '/\(symbol/ {flag=1} flag {print}' "$DEST_DIR/$NEW_NAME.kicad_sym" | sed '$d')

    new_content=$(echo "$new_content" | awk -v symbol_name="$NEW_NAME" -v counter="$COUNTER" '
        {
            if ($1 == "(symbol") {
                if(counter % 2 == 0) {
                    $2 = "\"" symbol_name "\""
                } else {
                    $2 = "\"" symbol_name "_0_1\""
                }
                counter++
            }
            if ($1 == "\"Footprint\"") {
                print $1
                print "\"pepy_sym_lib:" symbol_name "\""
                getline
                next
            }
            print
        }
    ')

    # Append the updated content to pepy_sym_lib.kicad_sym before the last parenthesis
    sed -i '$d' "pepy_sym_lib.kicad_sym"  # Remove the last parenthesis
    echo "$new_content" >> "pepy_sym_lib.kicad_sym"
    echo ")" >> "pepy_sym_lib.kicad_sym"  # Add the last parenthesis back
fi

# Modify the path of 3D models for the newly added component's footprint
footprint_directory="pepy_sym_lib.pretty"
footprint_file="$footprint_directory/$NEW_NAME.kicad_mod"

if [ -f "$footprint_file" ]; then
  # Get the footprint name and directory
  footprint_name=$(basename "$footprint_file" .kicad_mod)
  footprint_dir=$(dirname "$footprint_file")

  echo "footprint_file: $footprint_file"

  # Get the original 3D model path
  original_model_path=$(grep -oP '(?<=\(model ").*?(?=")' "$footprint_file")

  echo "original_model_path: $original_model_path"

  # Determine the new 3D model path
  new_model_path="original_components/$NEW_NAME/${footprint_name}.step"
  new_model_path_abs=$(readlink -f "$new_model_path")

  echo "new_model_path: $new_model_path_abs"

  # Read the file content
  content=$(cat "$footprint_file")

  # Update the 3D model path in the file content using awk
  content=$(echo "$content" | awk -v new="$new_model_path_abs" '{
    if ($1 == "(model") {
      sub(/".*"/, "\"" new "\"")
    }
    print
  }')

  # Write the modified content back to the file
  echo "$content" > "$footprint_file"

  echo "3D model path updated successfully for $footprint_name."
else
  echo "Footprint file $footprint_file not found."
fi
