#!/bin/bash

# Directory containing the kicad_sym files
directory="original_components"

# Output file
output_file="pepy_sym_lib.kicad_sym"

# Start the output with the header
echo "(kicad_symbol_lib" > "$output_file"
echo "  (version 20211014)" >> "$output_file"
echo "  (generator https://github.com/uPesy/easyeda2kicad.py)" >> "$output_file"

# Initialize a counter for symbols
symbol_counter=0

# Loop through all kicad_sym files in the directory and its subdirectories
find "$directory" -type f -name "*.kicad_sym" | while read -r file; do
  # Extract lines from the first occurrence of "(symbol" to the last closing parenthesis, excluding the last closing parenthesis
  content=$(awk '/\(symbol/ {flag=1} flag {print}' "$file" | sed '$d')

  # Get the folder name
  folder_name=$(basename "$(dirname "$file")")

  # Modify the symbol names to use the folder name and preserve postfix in second occurrence
  content=$(echo "$content" | awk -v folder="$folder_name" -v counter="$symbol_counter" '{
    if ($1 == "(symbol") {
      symbol_name = $2
      gsub("\"", "", symbol_name)
      sub(/_[^_]*$/, "", symbol_name)
      $2 = "\"" folder "\""
      
      counter++
      if (counter % 2 == 0) {
        $2 = "\"" folder "_0_1\""
      }

      print
      next
    }

    # Replace "easyeda2kicad" with "pepy_sym_lib: <folder_name>"
    if (index($0, "easyeda2kicad") > 0) {
      sub(/easyeda2kicad.*/, "pepy_sym_lib:"folder "\"", $0)
    }
    
    print
  }')

  # Update the symbol counter
  symbol_counter=$(echo "$content" | awk '/\(symbol/ {counter++} END {print counter}')

  # Append the modified content to the output file
  echo "$content" >> "$output_file"
done

# End the output with a closing parenthesis
echo ")" >> "$output_file"

# Print the result to the terminal
cat "$output_file"
