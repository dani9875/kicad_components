#!/bin/bash

# ------------------- Section I: Renaming footprint references to project library in .kicad_sym -------------------
# Find the only .kicad_sym file in the current directory
sym_file=( *.kicad_sym )

# Ensure exactly one file exists
if [[ ${#sym_file[@]} -ne 1 ]]; then
    echo "Error: Expected exactly one .kicad_sym file, found ${#sym_file[@]}."
    exit 1
fi

# Extract filename without extension
new_lib_name="${sym_file%.kicad_sym}"

# Perform the replacement in-place
sed -i "s/pepy_sym_lib/$new_lib_name/g" "$sym_file"

echo "Updated $sym_file: replaced 'pepy_sym_lib' with '$new_lib_name'"

# ------------------- Section II: Extract component names found in the schematic -------------------
# Find the .kicad_sch file in the current directory
sch_file=( *.kicad_sch )

# Ensure exactly one schematic file exists
if [[ ${#sch_file[@]} -ne 1 ]]; then
    echo "Error: Expected exactly one .kicad_sch file, found ${#sch_file[@]}."
    exit 1
fi

# Extract component names after semicolons from (symbol "lib_name:component_name")
temp_symbols=()

while read -r line; do
    if [[ "$line" =~ \(symbol\ \"[^\:]+:([^\"]+)\" ]]; then
        temp_symbols+=("${BASH_REMATCH[1]}")
    fi
done < "$sch_file"

# Output the extracted component names
# echo "Extracted component names from $sch_file:"
# for component in "${temp_symbols[@]}"; do
#     echo "$component"
# done

# ------------------- Section III: Copy referenced component directories to a new directory -------------------
# Define original components source directory
original_components_dir="/home/perseluspy/Documents/source/kicad_components/original_components"

# Create original_components directory in the script's execution location
mkdir -p "original_components"

#Extract referenced component directories from symbol lines in the .kicad_sym file
while read -r component_dir; do
    if [[ -d "$original_components_dir/$component_dir" ]]; then
        cp -r "$original_components_dir/$component_dir" "original_components/"
        echo "Copied $component_dir to original_components/"
    fi
done < <(grep -oP '(?<=\(symbol \")[^\"]+' "$sym_file" | sort -u)

# ------------------- Section IV: Find directories matching the pattern [CATEGORY] -------------------
# Initialize an array to store matching directory names
matching_dirs=()

# List and store only directories that match the pattern [text] with nothing after the closing bracket
for dir in "$original_components_dir"/*/; do
    dir_name=$(basename "$dir")
    if [[ "$dir_name" =~ ^\[[^\]]+\]$ ]]; then
        matching_dirs+=("$dir_name")
    fi
done

# # Print all matching directories
# for dir in "${matching_dirs[@]}"; do
#     echo "$dir"
# done

# ------------------- Section V: Find temp_symbols that match the pattern [CATEGORY] and copy them-------------------
# Track copied directories to avoid duplicate copying
copied_dirs=()

for dir in "${matching_dirs[@]}"; do
    bracket_content="${dir:1:-1}"  # Extract content inside brackets
    for symbol in "${temp_symbols[@]}"; do
        if [[ "$symbol" == "$dir"* ]] && [[ ! " ${copied_dirs[@]} " =~ " ${dir} " ]]; then
            if [[ -d "$original_components_dir/$dir" ]]; then
                cp -r "$original_components_dir/$dir" "original_components/"
                echo "Copied $dir to original_components/"
                copied_dirs+=("$dir")  # Mark as copied
            fi
        fi
    done
done

# ------------------- Section VI: Create test.pretty and copy relevant .kicad_mod files -------------------
# Define footprint source directory
footprint_source_dir="/home/perseluspy/Documents/source/kicad_components/pepy_sim_lib.pretty"

# Create test.pretty folder in the project directory
mkdir -p "test.pretty"

# Copy only the .kicad_mod files that match copied component directories
for component in "${copied_components[@]}"; do
    mod_file="$footprint_source_dir/$component.kicad_mod"
    if [[ -f "$mod_file" ]]; then
        cp "$mod_file" "test.pretty/"
        echo "Copied $component.kicad_mod to test.pretty/"
    fi
done