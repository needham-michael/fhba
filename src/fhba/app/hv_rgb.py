import geopandas as gpd
import holoviews as hv
import numpy as np
import xarray as xr
import panel as pn
from satpy.scene import Scene
import warnings

from fhba.image import nonlinear_enhancement

def load_scene(raw_granule_files, area_def, band_list, satpy_scene_reader,resampler='ewa'):

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")

        # There is currently a bug in Satpy that prevents loading and resampling
        # in a single step. As a workaround, create and load bands at full res
        # and then resample to the desired area before loading again. 
        scene_full = Scene(filenames=raw_granule_files, reader=satpy_scene_reader)
        scene_full.load(band_list)
        scene_regional = scene_full.resample(area_def,resampler=resampler)
        scene_regional.load(band_list)

    return scene_regional

def get_nir_red_img(scene,band_nir_str,band_red_str,overlay=None):

    nir = scene[band_nir_str].load()
    red = scene[band_red_str].load()

    lons, lats = scene[band_red_str].attrs['area'].get_lonlats()

    r = nonlinear_enhancement(255 * nir.values / 100) / 255
    g = nonlinear_enhancement(255 * red.values / 100) / 255
    b = np.sqrt((r * g))

    img = xr.concat(
        [xr.DataArray(c,dims=red.dims,coords=red.coords)for c in [r,g,b]],
        dim="band"
        ).transpose(...,"band")
    
    img = img.assign_coords({'band':np.arange(len(img.band))})

    rgb = hv.RGB((lons[0],lats[:,0],img.values)).opts(width=500,height=1000)

    if overlay is not None:

        overlay = hv.Path(gpd.read_file(overlay)).opts(color='w')

        rgb = rgb * overlay
        
    return rgb