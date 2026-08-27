from pathlib import Path
from typing import Dict, Tuple

import geopandas as gpd
import geoviews as gv
import numpy as np
import xarray as xr

from fhba.viz import nonlinear_enhancement

def shp2gdf(shpfile: Path) -> gpd.GeoDataFrame:
    try:
        return gpd.read_file(shpfile)
    except: 
        # Search one level down in case the shapefile is stored in a subfolder
        # like `/path/to/feature.shp/feature.shp`
        shpfile = list(shpfile.glob("*.shp"))[0]
        return gpd.read_file(shpfile)

def shp2gv(shpfile: Path, display_opts: Dict | None = None) -> gv.Shape:
    if display_opts is None:
        display_opts = {'line_color':'k','line_dash':'dashed','line_width':1}
    try:
        return gv.Shape.from_shapefile(shpfile).opts(**display_opts)
    except: 
        # Search one level down in case the shapefile is stored in a subfolder
        # like `/path/to/feature.shp/feature.shp`
        shpfile = list(shpfile.glob("*.shp"))[0]
        return gv.Shape.from_shapefile(shpfile).opts(**display_opts)

def nir_red_sqrt(
    ds: xr.Dataset,
    nir_band : str,
    red_band : str,
    **kwargs
) -> gv.element.geo.RGB:

    red = nonlinear_enhancement(255 * ds[red_band].values / 100) / 255
    nir = nonlinear_enhancement(255 * ds[nir_band].values / 100) / 255
    sqrt = np.sqrt(red * nir)

    return ds2rgb(ds,band_list=[nir,red,sqrt])

def nir_red_red(
    ds: xr.Dataset,
    nir_band : str,
    red_band : str,
    **kwargs
) -> gv.element.geo.RGB:

    red = nonlinear_enhancement(255 * ds[red_band].values / 100) / 255
    nir = nonlinear_enhancement(255 * ds[nir_band].values / 100) / 255

    return ds2rgb(ds,band_list=[nir,red,red])

def nir_red_mwir(
    ds: xr.Dataset,
    nir_band : str,
    red_band : str,
    mwir_band : str,
    **kwargs
) -> gv.element.geo.RGB:

    red = nonlinear_enhancement(255 * ds[red_band].values / 100) / 255
    nir = nonlinear_enhancement(255 * ds[nir_band].values / 100) / 255
    mwir = nonlinear_enhancement(255 * ds[mwir_band].values / 100) / 255

    return ds2rgb(ds,band_list=[mwir,red,nir])
    
def ds2rgb(
    ds: xr.Dataset,
    band_list: Tuple[np.array, np.array, np.array]
) -> gv.element.geo.RGB:

    bands = xr.concat(
        [xr.DataArray(b,dims=ds.dims,coords=ds.coords) for b in band_list],
        dim="band"
    ).transpose(...,"band")
    
    return gv.RGB(data=(ds.x,ds.y,bands.isel(band=0),bands.isel(band=1),bands.isel(band=2)),crs=ds.crs)

import holoviews as hv

def bm_rgb(
    bm : xr.Dataset,
    rgb_color : Tuple[int, int, int] 
) -> gv.element.geo.RGB:

    # Mask 0's to only show 1's
    bm = bm.where(bm != 0)

    return gv.RGB(data=(bm.x,bm.y,*[(bm.T*c/255) for c in rgb_color]),crs=bm.crs)

def initialize_userpoints(color,marker='x',point_locations=None,label=None):

    if point_locations is None:
        active_tools = ['point_draw']
        point_locations = ([], [],)

        points = hv.Points(point_locations,label=label).opts(color=color,marker=marker,size=20,)
        point_stream = hv.streams.PointDraw(data=points.columns(), source=points)

        userpoints = points.opts(active_tools=active_tools)

        return userpoints, point_stream

    else:
        points = hv.Points(point_locations,label=label).opts(color=color,marker=marker,size=20,)

        return points

def initialize_userpolys(color='red',alpha=0.5):

    polys = hv.Polygons(data=None,label='label').opts(color=color,alpha=alpha,active_tools=['poly_draw'])
    poly_stream = hv.streams.PolyDraw(source=polys)

    return polys, poly_stream