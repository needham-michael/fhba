
import importlib
import os
from time import perf_counter

import numpy as np
import rioxarray as rxr

from pyresample import image, geometry

import yaml

def read_config():

    config_file   = importlib.resources.files('fhba') / 'config.yaml'

    with open(config_file) as f:
        config = yaml.safe_load(f)
        config = {k:config[k] for k in config if not k.startswith('_')}

    return config

def get_nlcd_area_definition(nlcd_mask):

    lat_0 = nlcd_mask.spatial_ref.latitude_of_projection_origin  
    lat_1, lat_2 = nlcd_mask.spatial_ref.standard_parallel
    lon_0 = nlcd_mask.spatial_ref.longitude_of_central_meridian 
    ellps = "".join(nlcd_mask.spatial_ref.geographic_crs_name.split())
    x_0 = nlcd_mask.spatial_ref.false_easting
    y_0 = nlcd_mask.spatial_ref.false_northing
    a = nlcd_mask.spatial_ref.semi_major_axis
    b = nlcd_mask.spatial_ref.semi_minor_axis
    
    proj_str = f"+proj=aea +{lat_1=} +{lat_2=} +{lat_0=} +{lon_0=} +{x_0=} +{y_0=} +{a=} +{b=} +units=m +no_defs"
    
    # gt = [float(x) for x in nlcd_mask.spatial_ref.GeoTransform.split()]
    
    width = len(nlcd_mask.x.data)
    height = len(nlcd_mask.y.data)
    
    lower_left_x = min(nlcd_mask.x.data)
    lower_left_y = min(nlcd_mask.y.data)
    upper_right_x = max(nlcd_mask.x.data)
    upper_right_y = max(nlcd_mask.y.data)

    area_nlcd = geometry.AreaDefinition(
        area_id="NLCD",
        description="NLCD Albers Equal Area",
        proj_id="",
        projection=proj_str,
        width=width,
        height=height,
        area_extent=(lower_left_x, lower_left_y, upper_right_x, upper_right_y)
    )

    return area_nlcd

def main():

    print("Starting land cover mask processing...")

    start_time = perf_counter()

    config = read_config()

    try:
        nlcd_filedir = config['nlcd']['nlcd_filedir']
        nlcd_filename = config['nlcd']['nlcd_filename'] 

        if nlcd_filedir[:4] == "fhba":
            nlcd_input_file = importlib.resources.files(nlcd_filedir.replace("/",".")) / nlcd_filename
        else:
            nlcd_input_file = os.path.join(nlcd_filedir, nlcd_filename)
    except ModuleNotFoundError as e:
        raise FileNotFoundError("Could not find NLCD input file. Please ensure that the file 'Annual_NLCD_LndCov' file is located in the 'appdata/annual_nlcd' directory of the 'fhba.app' package.") from e

    print("Reading NLCD data and clipping to spatial extent defined in config.yaml...")

    spatial_name = config['spatial']['spatial_name']

    x0 = config['spatial']['min_lon']
    x1 = config['spatial']['max_lon']
    y0 = config['spatial']['min_lat']
    y1 = config['spatial']['max_lat']

    output_filename = importlib.resources.files(nlcd_filedir.replace("/",".")) / f"NLCD_LandMask_{spatial_name}.tif"

    print(f"Opening NLCD file: {nlcd_input_file}")
    nlcd = rxr.open_rasterio(nlcd_input_file).isel(band=0)
    
    print(f"Clipping NLCD file to spatial extent: : {x0}, {y0}, {x1}, {y1}")
    nlcd_clipped = nlcd.rio.clip_box(minx=x0,miny=y0,maxx=x1,maxy=y1,crs='epsg:4326')

    print("Masking NLCD data to include only land cover classes 71 (Grassland/Herbaceous) and 81 (Pasture/Hay)...")
    nlcd_land_mask = np.logical_or(
        nlcd_clipped == 71, nlcd_clipped == 81
    ).fillna(0)

    print("Saving land mask to file ...")
    nlcd_land_mask.rio.to_raster(output_filename,compress='LZMA',dtype="int16")

    end_time = perf_counter()
    elapsed_time = end_time - start_time
    print(f"Done. Elapsed time: {elapsed_time:.2f} seconds")

if __name__ == '__main__':
    
    main()
    