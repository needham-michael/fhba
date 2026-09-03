# Case Selection

The application opens into the Case Selection Pane, which has three sections allowing the user to **Load** an existing case, **Create** a new case, or **Delete** an old case. 

??? question "What is a Case?"
    A **Case** is simply a collection of data files and application configurations set by the user. A user could define different cases to perform burn mapping in a variety of different contexts. These include:

    - Specifying a different bounding box or spatial resolution (e.g., 500 vs. 375 meter grid spacing)
    - Different combinations of satellites or spectral bands
    - Different classification algorithims
    - Analysis during different times of the year (e.g., Spring vs. Fall burns)

---

## Load Existing Case

Use the dropdown to select a case, and then click the __`Load Case`__ button to proceed to the __[Analysis Pipeline](./Analysis/index.md)__. 

!!! info
    The Load Existing Case option will be **disabled** if the user has not yet created a case.

---

## Create New Case

The __Create New Case__ section allows the user to specify different configuration options for a new case.

### Casename

Use the __New Casename__ input to specify a unique name assigned to the case. This could be something like __`flinthills`__, __`fh-spring`__, or __`fh-fall2026`__.

There are currently no restrictions on the __`casename`__ parameter (e.g., length limit or forbidden characters) but keep in mind that the __`casename`__ is used to name files and folders.

### Spatial Resolution and Bounding Box

Use the __New Case Spatial Resolution Meters [dx, dy]__ input to specify the mapping grid spacing in the x- and y-direction in meters. The default of `500m x 500m` is based on the nominal grid spacing of the VIIRS level 2 products, but the user may choose to tweak this (e.g., to match the nominal 375m or 750m resolution of the VIIRS I- and M-Bands).

!!! info "For more information"

    - __[VIIRS Spectral Bands (NASA Earthdata)](https://www.earthdata.nasa.gov/data/instruments/viirs/spectral-bands)__

Use the __Bounding Box [minlon, minlat, maxlon, maxlat]__ input to specify the lower-left and upper-right coordinates of the bounding box grid.

- Once specified, use the __`Validate Bounding Box`__ button to preview the bounding box in the map pane and tweak as necessary, or use the __`Reset Bounding Box`__ button to delete the previous coordinates. 

*Note that the spatial resolution and bounding box are approximate and will be tweaked slightly by the reprojection algorithm which requires a whole-number of pixels in the x- and y-directions*.

### Data and Output Directories

Use the __New Case Data Directory__ and __New Case Output Directory__ inputs to specify the folders on your computer where data files will be written and used by the application. 

- The __Data Directory__ holds the large data files such as raw and reprojected satellite granules.

- The __Output Directory__ holds the daily preliminary and final burnmasks as well as seasonal-aggregate burnmasks.


The application will join the __Casename__ to the end of the entered __Data Directory__ to make a combined file path. For example if the user specifies `casename = "new_case"`,  `data_directory = "E:/USER/path/to/data_dir/"`, and `output_directory = "C:/USER/path/to/output_dir/"` the application will create the following directories as part of the case creation step:

```shell
# Data Directory
E:/USER/path/to/data_dir/new_case

# Output Directory
C:/USER/path/to/output_dir/new_case
```

!!! tip
    Multiple cases may share the same __Data Directory__ and __Output Directory__, so that different cases' files can be organized under the same parent directories.

The application treats the __Data Directory__ and __Output Directory__ separately so that large data files can be stored on an external file system, if available (e.g., on an `E:/` drive rather than the primary `C:/` drive). However the user can specify that a case utilize the same directory for both the __Data Directory__ and __Output Directory__, if desired, by selecting the `Same as Data Directory` checkbox, which disables the __New Case Output Directory__ input.


Once the directories have been entered, use the __`Validate Data Directory`__ and __`Validate Output Directory`__ (if applicable) buttons to ensure that the directories have been entered properly and are visible to the application.

*Note that the application will not create the Data Directory and Output Directory if they do not exist. The directories must be created manualy (e.g., through the system file explorer).*

---

## Delete Case

Use the following steps to delete an old case:

1. Select the `Enable Case Deletion` checkbox
2. Use the dropdown to select the case for deletion
3. Click the `Delete Case`
4. Click the final `Delete Case` in the popup that has appeared. __This cannot be undone!__

!!! danger "Deleting a case cannot be undone!"
    Deleting a case means that all files and folders associated with the case will be permenently removed. This includes:

    - Raw satellite granules
    - Processed / Reprojected granules
    - User-categorized burned and un-burned points
    - Preliminary and finalized daily burnmasks