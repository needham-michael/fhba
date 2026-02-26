# FHBA (Flint Hills Burned Area Tool)

## Setup

### Land cover mask

This tool relies on a landcover mased derived from the __[USGS National Land Cover Database](https://www.usgs.gov/centers/eros/science/national-land-cover-database)__. As seen in the image below, the Flint Hills primarily consists of NLCD categories __71 (Grasslands/Herbaceous)__ and __81 (Pasture/Hay)__. The included `fhba/process_landcover_mask.py` script generates an appropriate landcover mask so that only pixels coincident with these two NLCD categories are considered to have the potential to burn.

![](./_static/nlcd2024_mrlc_viewer.png)

