# Classify Pixels

As in the previous __[1. Download Granules](./download_granules.md)__ and __[2. Process Granules](./process_granules.md)__ sections, the user first selects a `Year` and `Satellite` from the dropdowns.

The user also should specify which classification methods should be used to classify pixels

| Method | Full Name |  Implementation | 
| ------ | --------- | ---------- |
| `eucl`[^1] | Euclidean Distance Classifier | __[sklearn.neighbors.KNeighborsClassifier](https://scikit-learn.org/stable/modules/generated/sklearn.neighbors.KNeighborsClassifier.html)__ | 
| `rforest` | Random Forest Classifier | __[sklearn.ensemble.RandomForestClassifier](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.RandomForestClassifier.html)__ | 
| `svm` | Support Vector Machines Classifier | __[sklearn.svm.SVC](https://scikit-learn.org/stable/modules/generated/sklearn.svm.SVC.html)__ | 

!!! Bug
    There is a known bug where clicking the `Next` button raises an issue on the subsequent page that the user "Must Select At Least One Classification Method" even though the case default classification methods are selected.

    The current work-around is to click the :octicons-x-circle-fill-24: for one of the methods and then to re-select the method before clicking the `Next` button.

## Load Composite

The next stage allows the user to select which RGB composite will be loaded for a given date (some options may require additional bands beyond the defaults). The user makes their selections and then clicks the `Load Image` button.

## Annotate and Classify Pixels

The next stage shows two interactive maps encompassing the case bounding box. The left map pane ("Annotation Pane") is populated by the RGB composite for the selected date and the right pane ("Burnmask Pane") is initially empty.

!!! Bug
    Currently the linking between the two map panes where zooming / panning in one pane is mirrored in the other pane breaks when the user generates or loads a burnmask. This will be fixed in a future update.

### Annotation Pane 

On the right-hand sidebar of the Annotation Pane are four icons which allow the user to identify pixels and regions to include in the burned/un-burned training set.

The top two `Point-Select` buttons allow the user to sort individual points into the training sets (red "X" for burned; white "+" for un-burned). The bottom two `Polygon-Select` buttons allow the user to draw polygonal boundaries identifying regions of pixels for the training sets (red polygons for burned; white polygons for un-burned).

The user should use these tools to specify the training set for a given date and click the `Save Points` button (and optionally `Overwrite Existing Points`) to export the points and polygons to a file. Note that all polygons are converted to points prior to saving.

| Button | Effect | 
| ------ | ------ |
| Load Points | Load previously-classified user points from file |
| Clear Points | Remove unsaved points from the map | 
| Reset Points | **Not Implemented** |
| Save Points | Export points to file (polygonal regions converted to points on the mapping grid)

!!! Bug
    There is a known bug if points have been loaded from file, additional points or polygons are added to the map, points are saved (overwriting existing points) and the `Load Points` button is clicked.

### Burnmask Pane

!!! tip "Optimal Number of Points"
    Pending

Once points have been saved from the burnmask pane, click the __`Generate Burnmask`__ button to use annotation points as a training set for the burnmask algorithm. The Application will apply each of the selected algorithms sequentially, and combine their output using a "Majority Voting" threshold to identify burned pixels. The threshold requires that a majority of the burnmask algorithms agree that a pixel was burned for the pixel to count as burned (e.g., 2 out of 3 methods).

The combined preliminary burnmask will then populate the Burnmask Pane and the user can use the `QA Points` and `QA Polygon` tools to refine the burnmask. Selecting points (with `QA Points`) or regions (with `QA Polygon`) will flip burned pixels to un-burned pixels, with no effect on previously un-burned pixels. The user can then click the `Apply QA Masking` button to incorporate these changes into the finalized burnmask before using the `Save Final Burnmask` to export the day's burnmask.

| Button | Effect | 
| ------ | ------ |
| Load Burnmask | Load a pre-existing preliminary burnmask from disk
| Generate Burnmask | Use points from Annotation Pane as training set for the burnmask algorithms |
| Apply QA Masking | Incorporate QA masking into the preliminary burnmask | 
| Reset QA Masking | Remove any QA masking from the preliminary burnmask |
| Save Final Burnmask | Export the QA'd burnmask to file |


Once the user has analyzed burnmasks for all desired dates, proceed to __[Aggregate Burnmasks](./aggregate.md)__ by clicking the `4. Aggregate Burnmasks` tab near the top of the screen.


[^1]: See __[this GitHub issue](https://github.com/needham-michael/fhba/issues/49)__