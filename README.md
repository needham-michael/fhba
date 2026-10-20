# FHBA 

The __Flint Hills Burned Area (FHBA)__ mapping tool is a Python-based application to categorize burned and unburned regions of the Flint Hills polar-orbitting satellite imagery.

__For instructions on how to download, setup, launch, and use the application, see the online [User's Guide](https://needham-michael.github.io/fhba/)__

## Features

#### Data Acquisition and Processing Pipeline

Download satellite granules from __[NASA EarthData](https://www.earthdata.nasa.gov/)__, extract specified imagery bands, and reproject to the user's mapping grid without leaving the application. 

#### Semi-Supervised Pixel Classification

Identify burned and unburned training regions, and let the application classify the entire image using a blended ensemble approach of different classification algorithms.

#### Full-Season Burn Mask Aggregation

Combine daily burn layers from multiple instruments into a cumulative "season-to-date" burn mask.

#### Multi-Instrument Support

Current or planned support for the following instruments 

| Platform | Instrument | Start Date | End Date |
| -------- | ---------- | ---------- | -------- |
| Terra | MODIS | 24 February 2000 | February 2027|
| Aqua | MODIS | 4 July 2002 | September 2027|
| Suomi-NPP | VIIRS | 19 January 2012 | November 1, 2026|
| NOAA-20 | VIIRS | 5 January 2018 | - |
| NOAA-21 | VIIRS | 10 February 2023 | - |
| *NOAA-22* | *VIIRS* | *expected 2027*| - |
| *NOAA-23* | *VIIRS* | *expected 2032*| - |
| Sentinel-3A | OLCI | 16 February 2016 | - |
| Sentinel-3B | OLCI | 25 April 2016 | - |
| *Sentinel-3C* | OLCI | *expected 2026* | - |

## License

Copyright (c) 2026 Michael Needham

Licensed under the MIT License. 
<hr>