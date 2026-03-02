# FHBA (Flint Hills Burned Area Tool)

## Setup

### Land cover mask

This tool relies on a landcover mased derived from the __[USGS National Land Cover Database](https://www.usgs.gov/centers/eros/science/national-land-cover-database)__. As seen in the image below, the Flint Hills primarily consists of NLCD categories __71 (Grasslands/Herbaceous)__ and __81 (Pasture/Hay)__. 

The included `fhba/process_landcover_mask.py` script generates an appropriate landcover mask so that only pixels coincident with these two NLCD categories are considered to have the potential to burn.

1. Download the Annual Land Cover Database for a recent year from the __[Multi Resolution Land Characteristics (MRLC) Consortium](https://www.mrlc.gov/data)__. Unzip the download and place the NLCD file 
2. Update the application config file to point to the NLCD file
3. Execute the python script (e.g., `uv run /fhba/process_landcover_mask.py`)

![](./_static/nlcd2024_mrlc_viewer.png)

