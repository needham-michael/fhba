
import importlib
import os
from time import perf_counter

import numpy as np
import rioxarray as rxr

import yaml

def read_config():

    config_file   = importlib.resources.files('fhba') / 'config.yaml'

    with open(config_file) as f:
        config = yaml.safe_load(f)
        config = {k:config[k] for k in config if not k.startswith('_')}

    return config

def main():

    print("Starting land cover mask processing...")

    start_time = perf_counter()

    try:
        nlcd_filedir = config['nlcd']['nlcd_filedir']
        nlcd_filename = config['nlcd']['nlcd_filename'] 

        if nlcd[:4] == "fhba":
            nlcd_input_file = importlib.resources.files(nlcd_filedir.replace("/",".")) / nlcd_filename
        else:
            nlcd_input_file = os.path.join(nlcd_filedir, nlcd_filename)
    except ModuleNotFoundError as e:
        raise FileNotFoundError("Could not find NLCD input file. Please ensure that the file 'Annual_NLCD_LndCov' file is located in the 'appdata/annual_nlcd' directory of the 'fhba.app' package.") from e

    print("Reading NLCD data and clipping to spatial extent defined in config.yaml...")
    
    config = read_config()

    spatial_name = config['spatial']['spatial_name']

    x0 = config['spatial']['min_lon']
    x1 = config['spatial']['max_lon']
    y0 = config['spatial']['min_lat']
    y1 = config['spatial']['max_lat']


    print(f"Opening NLCD file: {nlcd_input_file}")
    nlcd = rxr.open_rasterio(nlcd_input_file).isel(band=0)
    
    print(f"Clipping NLCD file to spatial extent: : {x0}, {y0}, {x1}, {y1}")
    nlcd_clipped = nlcd.rio.clip_box(minx=x0,miny=y0,maxx=x1,maxy=y1,crs='epsg:4326')

    print("Masking NLCD data to include only land cover classes 71 (Grassland/Herbaceous) and 81 (Pasture/Hay)...")
    nlcd_land_mask = np.logical_or(
        nlcd_clipped == 71, nlcd_clipped == 81
    ).fillna(0)

    output_filename = importlib.resources.files("fhba.app.appdata.nlcd") / f"NLCD_LandMask_{spatial_name}.tif"

    print("Saving land mask to file ...")
    nlcd_land_mask.rio.to_raster(output_filename,compress='LZMA',dtype="int16")

    end_time = perf_counter()
    elapsed_time = end_time - start_time
    print(f"Done. Elapsed time: {elapsed_time:.2f} seconds")

if __name__ == '__main__':
    
    main()
    