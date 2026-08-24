from pathlib import Path
import rasterio
import xarray as xr
from pyresample import create_area_def
from pyresample.geometry import AreaDefinition
from pyresample.kd_tree import XArrayResamplerNN
from rasterio.transform import from_bounds

def get_raster_area_def(raster : xr.Dataset) -> AreaDefinition:
    
    xlim = [float(z) for z in [raster.x.min(),raster.x.max()]]
    ylim = [float(z) for z in [raster.y.min(),raster.y.max()]]

    src_area_def = create_area_def(
        area_id="",
        projection=raster.spatial_ref.crs_wkt,
        shape=raster.squeeze().shape,
        area_extent = [xlim[0],ylim[0],xlim[1],ylim[1]]
    )

    return src_area_def

    
def reproject_raster(raster : xr.Dataset,target_area_def : AreaDefinition) -> xr.Dataset:

    src_area_def = get_raster_area_def(raster)

    resampler = XArrayResamplerNN(
        source_geo_def=src_area_def,
        target_geo_def=target_area_def,
        radius_of_influence=90
    )

    # The following line appears to fix a bug within pyresample. Otherwise the 
    # following get_sample_from_neighbour_info(...) function call fails.
    resampler.index_array = resampler.get_neighbour_info()[2]

    return resampler.get_sample_from_neighbour_info(
        data=raster.squeeze(),fill_value=raster._FillValue)

def write_raster(raster : xr.Dataset,output_filename : Path,target_area_def : AreaDefinition) -> None:

    meta = {
        "driver": "GTiff",
        "height": target_area_def.height,
        "width": target_area_def.width,
        "count": 1,
        "dtype": raster.dtype,
        "crs": target_area_def.crs,
        "transform": from_bounds(
            *target_area_def.area_extent,target_area_def.width,target_area_def.height),
    }

    with rasterio.open(output_filename, "w", **meta) as dst:
      dst.write(raster, 1)