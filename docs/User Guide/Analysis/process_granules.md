# Processing Granules

As in the previous section __[1. Download Granules](./download_granules.md)__ the user first selects a `Year` and `Satellite` from the dropdowns.

The user also should specify which (if any) additional reflectance bands should be included in the analysis beyond the minimal required bands:

| Instrument | Required Bands | Optional Bands | Reference | 
| ---------- | -------------- | -------------- | --------- |
| VIIRS | `I01`, `I02` | `I03`-`I05`; `M01`-`M16` | __[VIIRS Spectral Bands (NASA EarthData)](https://www.earthdata.nasa.gov/data/instruments/viirs/spectral-bands)__ |

!!! info "Default Bands"

    If the user changes which additional bands are included, it is recommended to click the __`Save as Case Default`__ button which will pre-load the same additional bands every time a satellite with the same instrument is selected (e.g., Suomi-NPP and NOAA-20 satellites which both carry VIIRS).

## Mosaic

!!! warning "Mosaicking Not Implemented"
    Currently the application does not allow for any mosaicking options 
    besides `stack`, where overlapping granules are mosaicked by taking
    the most recent (i.e., the image which occurred later in the day)
    image.
    
    This only impacts a small number of dates in which images from 
    two consecutive orbits both intersect the case bounding box on the
    same date.

Click `Next` to continue to the next stage.

## Reproject Granules

For reference, the reprojection stage provides a table listing the downloaded granules (from the previous section) along with a flag to indicate whether the granule has already been processed. 


The user should use the __Reprojection Date Range__ dropdown calendar to specify which granules to include. Then, click the __`Reproject Granules`__ button to begin reprojecting granules.

The application will skip processing a granule if it has already been processed (i.e., if the `Processed?` column is `True`) unless the user has selected the `Overwrite` checkbox.

Once the reprojection is complete, proceed to __[Classify Granules](./classify.md)__ by clicking the `3. Classify Granules` tab near the top of the screen.