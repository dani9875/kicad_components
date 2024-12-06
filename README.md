# kicad_components

*Note: Libary created used and optimized on KiCAD 8.0*

# How to start using the component library
## 1. Clone the Repository

Clone the repository to your local machine using the following command:

```bash
git clone git@github.com:dani9875/kicad_components.git
```

## 2. Add Symbol Libraries

1. Open **KiCad**.
2. Navigate to **Preferences** > **Manage Symbol Libraries**.
3. In the dialog:
   - Click the **Add Library** button (the `+` icon).
   - Locate and select the `pepy_sim_lib.kicad_sym` file from the cloned repository.
   - Choose **Global** or **Project** scope based on your preference.
4. Save the changes.

## 3. Add Footprint Libraries

1. Open **KiCad**.
2. Navigate to **Preferences** > **Manage Footprint Libraries**.
3. In the dialog:
   - Click the **Add Library** button (the `+` icon).
   - Locate and select the `pepy_sim_lib.pretty` folder from the cloned repository.
   - Choose **Global** or **Project** scope based on your preference.
4. Save the changes.

## 4. Configure Paths

1. Open **KiCad**.
2. Navigate to **Preferences** > **Configure Paths**.
3. In the dialog:
   - Click the **Add Path** button (the `+` icon).
   - Add an environment name called **`ORIGINAL_COMPONENTS`**.
   - Associate it with the `original_components` folder from the cloned repository.
4. Save the changes.

# How to add new components to the library

Manually handled:
- Test points
- Mounting holes


## Release notes

### Version: v4
Changelog:
- 10P 2x5 1.27 mm pin header added to library

### Version: v3
Changelog:
- 10 nF YAGEO capacitor added
- 100 MOhm Visay resistor added
- Resistor with 1206 size (symbol and footprint) added

### Version: v2
Changelog:
- Param generic resistor/capacitor compnent
- FUSE added (generic)
- Adding information to many components

### Version: v1
Changelog:
- Initial component library


## List of potentional improvements
- [ ] Add installing steps in how to section
- [ ] Don't run update on original_components folder when running "add_component.sh"
- [ ] Rewiring 3D model files easily 
- [ ] Consumpotion and other parameters for the components?
- [ ] Bespoke inventory and generated BOM footprint interface adapation

