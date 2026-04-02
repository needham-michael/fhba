
import importlib
import os
from time import perf_counter

import numpy as np
import rioxarray as rxr

from scipy import ndimage as ndi

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

def flip_singletons(binary: np.ndarray, diagonals: bool = True, which: str = "both", min_size: int = 1) -> np.ndarray:
    """
    Flip small isolated connected components in a 2D binary raster.

    Parameters
    ----------
    binary : np.ndarray
        2D boolean array (True/False). Non-boolean inputs will be cast to bool.
    diagonals : bool, default True
        If True, use 8-connectivity (diagonal neighbors included).
        If False, use 4-connectivity (no diagonal neighbors).
    which : {"true", "false", "both"}, default "both"
        Which components to flip:
        - "true": flip only small True components to False
        - "false": flip only small False components to True
        - "both": flip both small True and small False components
    min_size : int, default 1
        Minimum component size to keep. Components strictly smaller than this
        value will be flipped. Default of 1 preserves the original behaviour
        of removing only single-pixel (size == 1) features. Set to 5 to
        remove all connected components with fewer than 5 pixels.

    Returns
    -------
    np.ndarray
        A boolean array with the same shape as `binary`, with small components flipped.

    Notes
    -----
    - Connectivity:
        * 8-connectivity considers neighbors in a 3x3 block.
        * 4-connectivity considers only N, S, E, W neighbors.
    """
    arr = np.asarray(binary, dtype=bool)
    if arr.ndim != 2:
        raise ValueError("Input must be a 2D array")

    # Structuring element for connectivity
    if diagonals:
        structure = np.ones((3, 3), dtype=bool)  # 8-connectivity
    else:
        structure = np.array([[0, 1, 0],
                              [1, 1, 1],
                              [0, 1, 0]], dtype=bool)  # 4-connectivity

    # Work on a copy for output; derive masks from the original to avoid interference
    out = arr.copy()

    which = which.lower()
    if which not in {"true", "false", "both"}:
        raise ValueError("`which` must be one of {'true', 'false', 'both'}")

    # Small True components (size < min_size)
    if which in {"true", "both"}:
        labels_t, _ = ndi.label(arr, structure=structure)
        sizes_t = np.bincount(labels_t.ravel())
        if sizes_t.size:
            sizes_t[0] = 0  # never treat background as a small component
        small_labels_t = np.flatnonzero(sizes_t < min_size)
        if small_labels_t.size:
            mask_small_true = np.isin(labels_t, small_labels_t)
            out[mask_small_true] = False

    # Small False components (size < min_size) — label the complement
    if which in {"false", "both"}:
        labels_f, _ = ndi.label(~arr, structure=structure)
        sizes_f = np.bincount(labels_f.ravel())
        if sizes_f.size:
            sizes_f[0] = 0  # never treat background as a small component
        small_labels_f = np.flatnonzero(sizes_f < min_size)
        if small_labels_f.size:
            mask_small_false = np.isin(labels_f, small_labels_f)
            out[mask_small_false] = True

    return out

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
    