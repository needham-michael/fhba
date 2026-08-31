"""Module controlling preprocessing of National Land Cover Database raster"""
from pathlib import Path
from typing import Tuple

import cartopy.crs as ccrs
import geopandas as gpd
import numpy as np
import rioxarray as rxr
import xarray as xr
import shapely

from scipy import ndimage as ndi

from fhba.reproject import create_target_area_def, reproject_raster
from fhba.schemas import Registry

def get_grassland_pasture_mask(nlcd_raster : xr.DataArray) -> xr.DataArray:
    return np.logical_or(nlcd_raster == 71, nlcd_raster == 81).fillna(0)

def get_openwater_mask(nlcd_raster : xr.DataArray) -> xr.DataArray:

    openwater_mask = (nlcd_raster == 11).fillna(0)

    # Apply binary dilation on the inverted resampled openwater mask (0-open water, 
    #  to remove small features, then apply binary dilation 4 times on the0
    # resultant field to make a large buffer around large features
    openwater_mask_small_features = ndi.binary_dilation(
        1-ndi.binary_dilation(1-openwater_mask),
        iterations=4) 

    # Apply this interim mask to the original resampled field to retain the finer-
    # scale features of the large water bodies
    mask_interim = openwater_mask_small_features * openwater_mask

    # Apply a final binary dilation to extend the feature edges around large features
    mask_final = ndi.binary_dilation(mask_interim,iterations=1)
    
    return xr.DataArray((1-mask_final),dims=nlcd_raster.dims,coords=nlcd_raster.coords)

def preprocess_nlcd(
        registry : Registry, nlcd_file_fullres : Path, compute : bool = True
        ) -> Tuple[xr.DataArray, xr.DataArray]:
    """Clip and reproject NLCD to target area; get landcover masks"""
    
    target_area_def = create_target_area_def(
        casename=registry.casename,
        bounding_box=registry.bounding_box,
        resolution=registry.resolution,
        epsg=registry.epsg,
        epsg_units=registry.epsg_units
    )

    bbox = target_area_def.area_extent
    bbox = shapely.Polygon(shell=[
                (bbox[0],bbox[1]),
                (bbox[0],bbox[3]),
                (bbox[2],bbox[3]),
                (bbox[2],bbox[1]),
    ])
    
    bbox = gpd.GeoDataFrame(geometry=[bbox],crs=ccrs.epsg(registry.epsg))

    nlcd_fullres = rxr.open_rasterio(nlcd_file_fullres)
    nlcd_clipped = nlcd_fullres.rio.clip(bbox.geometry.values,crs=bbox.crs,from_disk=True,drop=True)

    nlcd_reproj = reproject_raster(raster=nlcd_clipped,target_area_def=target_area_def)

    if compute:
        nlcd_reproj = nlcd_reproj.compute()

    grassland_pasture_mask = get_grassland_pasture_mask(nlcd_reproj)
    openwater_mask = get_openwater_mask(nlcd_reproj)

    return grassland_pasture_mask, openwater_mask, target_area_def