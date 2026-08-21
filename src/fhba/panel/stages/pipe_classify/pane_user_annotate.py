from functools import lru_cache
from pathlib import Path
from typing import Dict, Tuple

import cartopy.crs as ccrs
import geopandas as gpd
import geoviews as gv
import holoviews as hv
import numpy as np
import pandas as pd
import panel as pn
import param
import shapely
import xarray as xr

from fhba.panel.utils import style
from fhba.viz import (nir_red_sqrt, nir_red_red, nir_red_mwir, shp2gv)

class PaneUserAnnotate(param.Parameterized):

    year = param.Integer()
    satellite_full = param.String()
    satellite = param.String()
    registry = param.Parameter()
    valid_max_date = param.String() # dates stored as strings like "YYYY-MM-DD"
    valid_min_date = param.String()
    sat_info = param.Parameter()
    sat_band_subset = param.List()
    classification_methods = param.List()
    granules = param.Parameter()

    def __init__(self,granules,rgb_composite_fn,**params):
        super().__init__(**params)
        self.granules = granules
        self._rgb_composite_fn = rgb_composite_fn
        self._get_style()
        self._build_user_annotate_pane()

        self._layout = self._user_annotate_pane_layout

    def _build_user_annotate_pane(self):
        # self._initialize_usergeom()
        self._loading_icon = pn.widgets.LoadingSpinner(value=False,size=35)
                

        self._loadpts_button = pn.widgets.Button(name="Load Points",on_click=self._load_points,**self.button_primary)
        self._clrpts_button = pn.widgets.Button(name="Clear Points",on_click=self._clear_points,**self.button_primary)
        self._reset_button = pn.widgets.Button(name="Reset Points",on_click=self._reset_points,**self.button_warning)
        self._export_button = pn.widgets.Button(name="Save Points",on_click=self._export_points,**self.button_success)
        self._export_overwrite_checkbox = pn.widgets.Checkbox(name="Overwrite Existing Points?",value=False)

        self._user_select_widgets = pn.WidgetBox(
            pn.pane.Markdown("## User Selection Controls"),
            pn.Row(
                self._loadpts_button,
                self._clrpts_button,
                self._reset_button,
                pn.Column(
                    self._export_button,
                    self._export_overwrite_checkbox
                ),
                self._loading_icon
            )
        )

        self._maptiles = gv.tile_sources.CartoLight.opts(
            xlim=(self.registry.epsg_extent[0],self.registry.epsg_extent[2]),
            ylim=(self.registry.epsg_extent[1],self.registry.epsg_extent[3]),
            )

        self._county_overlay = shp2gv(self.registry.county_shp)
        self._hvrgb = None

        self._user_annotate_pane = pn.pane.HoloViews(self._maptiles * self._county_overlay,min_height=800,min_width=800)
        self._user_annotate_pane_title = pn.pane.Markdown("")
        self._user_annotate_pane_layout = pn.Column(
            self._user_annotate_pane_title,
            self._user_select_widgets,
            self._user_annotate_pane,
        )

    def _load_img(self,event,date,composite,init_usergeom=True):
        self._selected_date = date
        self._selected_composite = composite
        self._user_select_widgets.disabled = True
        if init_usergeom:
            self._initialize_usergeom()

        self._user_annotate_pane_title.object = f"## {self.satellite} | {self._selected_date} | {self._selected_composite} RGB Composite"
        self._selected_granule = self.granules[self._selected_date]

        self._hvrgb = None
        if self.sat_info.instrument == 'viirs':
            self._hvrgb = self._load_img_viirs(
                date=self._selected_date,
                composite=self._selected_composite
                )
        else:
            raise NotImplementedError(f"Implement RGB Composite for {self.sat_info.instrument}")

        if self._hvrgb is not None:
            self._brn_point_stream.source = self._brn_points
            self._unb_point_stream.source = self._unb_points
            self._brn_poly_stream.source = self._brn_polys
            self._unb_poly_stream.source = self._unb_polys
            self._classification_overlay = (self._brn_points * self._unb_points * self._brn_polys * self._unb_polys)
            
            self._user_annotate_pane.object = self._maptiles * self._hvrgb * self._county_overlay * self._classification_overlay
        self._user_select_widgets.disabled = False

    @lru_cache(maxsize=3)
    def _load_img_viirs(self,date,composite):
        granule_manager = self.granules[date]
        _viirs_band_names = {
            'nir_band':"I01",
            'red_band':"I02",
            'mwir_band':"M11"
        }

        self._selected_ds = xr.open_dataset(granule_manager.files.reproj_granule)
        self._selected_ds = self._selected_ds.load()
        self._selected_ds.attrs['crs'] = ccrs.epsg(self.registry.epsg)

        fn = self._rgb_composite_fn[composite]

        return fn(ds=self._selected_ds,**_viirs_band_names)

    def _initialize_usergeom(self):
        self._brn_points, self._brn_point_stream = initialize_userpoints(
            crs=ccrs.epsg(self.registry.epsg),tooltip='Burned Points')
        self._unb_points, self._unb_point_stream = initialize_userpoints(
            crs=ccrs.epsg(self.registry.epsg),tooltip='Unburned Points',color='w',marker='+')
        self._brn_polys, self._brn_poly_stream = initialize_userpoly(
            crs=ccrs.epsg(self.registry.epsg),tooltip='Burned Polygons')
        self._unb_polys, self._unb_poly_stream = initialize_userpoly(
            crs=ccrs.epsg(self.registry.epsg),tooltip='Unburned Polygons',color='w')

    def _clear_points(self,event):
        self._loading_icon.value = True
        self._user_select_widgets.disabled = True
        self._initialize_usergeom()
        self._load_img(event,date=self._selected_date,composite=self._selected_composite,init_usergeom=True)
        self._loading_icon.value = False
        self._user_select_widgets.disabled = False

    def _reset_points(self,event):
        pn.state.notifications.info("Not yet implemented")

    def _load_points(self,event):
        print("Within _load_points")
        
        self._loading_icon.value = True
        self._user_select_widgets.disabled = True
        def apply_points(target,source):
            target.data['Longitude'] = [p.x for p in source.geometry]
            target.data['Latitude'] = [p.y for p in source.geometry]

        input_file = self._selected_granule.files.user_pts
        exit_fn = False
        if input_file is None:
            exit_fn = True
        elif not input_file.exists():
            exit_fn = True
        if exit_fn:
            self._loading_icon.value = False
            self._user_select_widgets.disabled = False
            pn.state.notifications.info("No Previous Points Found")
            return

        pn.state.notifications.info("Loading Previous Points")
        gdf_isburned, gdf_unburned = load_points(input_file)
        print(f"{gdf_isburned = }")
        print(f"{gdf_unburned = }")

        apply_points(self._brn_points,gdf_isburned)
        apply_points(self._unb_points,gdf_unburned)
        self._load_img(event,date=self._selected_date,composite=self._selected_composite,init_usergeom=False)
        self._loading_icon.value = False
        self._user_select_widgets.disabled = False

    def _export_points(self,event):
        self._loading_icon.value = True
        self._user_select_widgets.disabled = True
        if self._selected_granule.is_user_categorized:
            if not self._export_overwrite_checkbox.value:
                pn.state.notifications.warning(f"Points Already Exist for {self._selected_date}. Select 'Overwrite' to Re-Export")
                self._loading_icon.value = False
                self._user_select_widgets.disabled = False
                return
            pn.state.notifications.warning("Overwriting Existing Points")

        self._userselect2gdf()

        userpts_dir = self.registry.path_usrpt / str(self.year) / self.satellite
        userpts_dir.mkdir(exist_ok=True,parents=True)
        userpts_filename = userpts_dir / f"{self.registry.casename}_userpts_{self.satellite}_{self._selected_date.replace("-","")}.geojson"

        self._userpts_gdf.to_file(userpts_filename,driver='GeoJSON',index=False)

        print(f"# Update granule and registry; save updated registry to json")
        self._selected_granule.is_user_categorized = True
        self._selected_granule.files.user_pts = userpts_filename
        self.registry.to_json()

        self._loading_icon.value = False
        self._user_select_widgets.disabled = False
        pn.state.notifications.info(f"Points Exported for {self._selected_date}.")

    def _userselect2gdf(self):
        self._userpts2gdf()
        self._userpoly2gdf()
        self._userpts_gdf = pd.concat(
            [self._brn_points_gdf,self._brn_poly_gdf,self._unb_points_gdf,self._unb_poly_gdf]
        ).reset_index().sort_values(by='isBurned')

        self._userpts_gdf.crs = self._selected_ds.crs
        self._userpts_gdf['isBurned'] = self._userpts_gdf['isBurned'].astype(int)

    def _userpts2gdf(self,xname='Longitude',yname='Latitude'):
        def _pts2gdf(stream,xname,yname,isBurned):
            # THIS SHOULD BE REWRITTEN TO HAVE A MORE CLEARLY DEFINED NULL CASE
            # INSTEAD OF RELYING ON THE TRY/EXCEPT BLOCK
            try:
                return gpd.GeoDataFrame(
                    data={'isBurned':np.ones_like(stream.data[xname]) * isBurned},
                    geometry=shapely.points(stream.data[xname],stream.data[yname])
                )
            except:
                print(f"No valid point data found: {isBurned = }")
                print(f"{stream.data = }")
                print("No point data?")
                print("-"*79)
                return gpd.GeoDataFrame(geometry=[],data={'isBurned':[]})

        self._brn_points_gdf = _pts2gdf(self._brn_point_stream,xname,yname,isBurned=1)
        self._unb_points_gdf = _pts2gdf(self._unb_point_stream,xname,yname,isBurned=0)

    def _userpoly2gdf(self):
        def _poly2gdf(stream,isBurned):
            # THIS SHOULD BE REWRITTEN TO HAVE A MORE CLEARLY DEFINED NULL CASE
            # INSTEAD OF RELYING ON THE TRY/EXCEPT BLOCK
            try:
                polypts = poly2pts(ds=self._selected_ds,poly_data=stream.data)
                return gpd.GeoDataFrame(
                    data={'isBurned':np.ones_like(polypts) * isBurned},
                    geometry=polypts
                )
            except: 
                print(f"No valid polygon data found: {isBurned = }")
                print(f"{stream.data = }")
                print("No polygon data?")
                print("-"*79)
                return gpd.GeoDataFrame(geometry=[],data={'isBurned':[]})

        self._brn_poly_gdf = _poly2gdf(self._brn_poly_stream,isBurned=1)
        self._unb_poly_gdf = _poly2gdf(self._unb_poly_stream,isBurned=0)

    def _get_style(self):
        style_dict = style()
        for key in style_dict:
            setattr(self,key,style_dict[key])

    # For some reason need to call self.__panel__() to display this
    # since it is a page element and not an independent pipeline stage
    def __panel__(self):
        return self.panel()

    def panel(self):
        print("Within self.panel")
        return self._layout


def load_points(
    userpts_file : Path
) -> Tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:

    gdf = gpd.read_file(userpts_file)
    gdf_isburned = gdf[gdf['isBurned']==1]
    gdf_unburned = gdf[gdf['isBurned']==0]

    return (gdf_isburned, gdf_unburned)
    

def initialize_userpoints(
    crs : ccrs.CRS, 
    color : str='r',
    marker: str='x',
    tooltip: str | None = None,
):

    point_opts = dict(color=color,marker=marker,size=20,)
    points = gv.Points(([], [],),crs=crs).opts(**point_opts)
    point_stream = hv.streams.PointDraw(data=points.columns(),source=points,tooltip=tooltip)
    points = points.opts(active_tools=['point_draw'])

    return points, point_stream

def initialize_userpoly(
    crs : ccrs.CRS, 
    color : str='r',
    tooltip: str | None = None 
):

    poly_opts = dict(color=color,alpha=0.5)
    polys = gv.Polygons(data=None,crs=crs).opts(**poly_opts)
    poly_stream = hv.streams.PolyDraw(data=polys.columns(),source=polys,tooltip=tooltip)
    polys = polys.opts(active_tools=['poly_draw'])

    return polys, poly_stream

def poly2pts(
    ds : xr.Dataset,
    poly_data : Dict
) -> np.array:
    """Identifies points on dataset grid that fall within polygons"""

    # Convert gridcell coords into individual ordered pairs
    xgrid,ygrid = np.meshgrid(ds.x,ds.y)
    xgrid_flat = xgrid.ravel()
    ygrid_flat = ygrid.ravel()
    points = shapely.points(xgrid_flat,ygrid_flat)

    polys = shapely.MultiPolygon(
        [shapely.Polygon(shell=[(x,y) for x,y in zip(xs,ys)]) for xs,ys in zip(poly_data['xs'],poly_data['ys'])]
    )

    # Return arrays of xs, ys for points within polygons
    mask = shapely.contains(polys,points)
    return shapely.points(xgrid_flat[mask], ygrid_flat[mask])