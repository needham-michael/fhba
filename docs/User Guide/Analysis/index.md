# Analysis Pipeline

After the user has __[Selected a Case](../case_select.md)__, the application moves to the __Analysis Pipeline__, which controls the entire span of the analysis from downloading granules to exporting a finalized burnmask. The pipeline is separated into four separate sections:

__[`1. Download Granules`](./download_granules.md)__

:   Truecolor image previews are downloaded from NASA worlview. The user
    then categorizes images from __Fully Clear__ to __Fully Cloudy__ and
    finally utilizes NASA Earthdata to download satellite granules with
    the raw reflectances and per-pixel cloud threshold.

    
__[`2. Process Granules`](./process_granules.md)__

:   The User specifies which spectral bands to include for further analysis 
    and the raw satellite granules downloaded in the first step are re-
    projected to the case mapping grid (see __[Case Setup](../case_select.md#spatial-resolution-and-bounding-box)__).

    !!! warning "Mosaicking Not Implemented"
        Currently the application does not allow for any mosaicking options 
        besides `stack`, where overlapping granules are mosaicked by taking
        the most recent (i.e., the image which occurred later in the day)
        image.
        
        This only impacts a small number of dates in which images from 
        two consecutive orbits both intersect the case bounding box on the
        same date.

    
__[`3. Classify Pixels`](./classify.md)__

:   The user utilizes Point-Select and Polygon-Select tools to identify 
    burned and un-burned regions of each satellite image. These regions
    are used as the training set on one or more classification algorithms
    which then categorize all pixels in the reprojected image as burned
    or un-burned. The user then has the opportunity to preview the
    preliminary burnmask, perform QA (i.e., to flip pixels / regions from
    burned to un-burned) and export the final burnmask for that date.
    
__[`4. Aggregate Burnmasks`](./aggregate.md)__

:   The user aggregates burnmasks across a date range (e.g., from 
    2026-02-15 through 2026-04-15) to generate a composite burnmask based
    on images from one or more satellites. The composite burnmask is then
    used to tally the total number of acres burned across the entire region
    and on a county-by-county basis