import logging
import os
import importlib

import holoviews as hv
import geopandas as gpd
import numpy as np
import pandas as pd
import panel as pn
import param
import rasterio
import rioxarray as rxr

from fhba.app.utils import get_instructions, initialize_userpoints, initialize_userpolys, polys2gdf

logger = logging.getLogger(__name__)

class StageClassify(param.Parameterized):

    year = param.Integer()
    satellite = param.String()
    registry = param.Parameter()
    gm = param.Parameter()

    @param.depends('year','satellite','registry', 'gm')
    def table_pane(self,return_df=False):

        gm_df = self.gm.to_df().reset_index()
        gm_df = gm_df.rename(columns={'index':'date'})
        gm_df = gm_df[gm_df['analysis_status'] != "Unanalyzed"]

        table = pn.pane.DataFrame(
            gm_df[['date','analysis_status','categorization_status']], index=False, 
            width=600
        )

        if return_df:
            return table, gm_df
        
        return table

    # @param.depends('year','satellite','registry','gm')
    # def categorize_pixels(self,date):

    #     pass

    @param.depends('year','satellite','registry','gm')
    def view(self):
        instr = get_instructions("06_eucl_categ.md",instr_width=250)

        table, gm_df = self.table_pane(return_df=True)

        analysis_date_selector = pn.widgets.Select(
            name="Post-fire Analysis Date",
            options=gm_df['date'].tolist(),
            width=200,
        )

        # Pre-fire reference date for dNBR computation
        # clear_dates = self.gm.get_clear_processed_dates()
        pre_fire_date_selector = pn.widgets.Select(
            name="Pre-fire Reference Date (for dNBR)",
            options=['None'] + gm_df['date'].tolist(),
            value='None',
            width=220,
            visible=False,  # Initially hidden; only show when dNBR option is implemented
        )

         # Classification method selector
        method_selector = pn.widgets.Select(
            name="Classification Method",
            options=['eucl', 'rf', 'svm'],
            value='eucl',
            width=120,
        )
        method_help = pn.pane.Markdown(
            "_eucl_ – Euclidean distance (fast, no training required)  \n"
            "_rf_ – Random Forest (recommended for accuracy)  \n"
            "_svm_ – Support Vector Machine",
            width=300,
        )

        load_img_button = pn.widgets.Button(
            name="Load Analysis Date Image", button_type="primary", width=150
        )

        categorize_pixel_button = pn.widgets.Button(
            name="Categorize Pixels", button_type="primary", width=150
        )

        export_burnmask_button = pn.widgets.Button(
            name="Export Burnmask", button_type="primary", width=150
        )

        county_overlay = self.gm.get_county_overlay().opts(width=500, height=1000)

        hv_pane = pn.pane.HoloViews(county_overlay)
        burnmask_pane = pn.pane.HoloViews(county_overlay)

        loading = pn.indicators.LoadingSpinner(name="Loading Image...", width=200, height=50,visible=False,value=False)

        polys_areamask, poly_stream = initialize_userpolys()

        def update_hv_rgb_with_points(event):
            date = analysis_date_selector.value

            print(f"Updating HV RGB with {date = }")  # Debugging statement

            points_csv_file = os.path.join(
                self.gm.userpts_dir,f"./{self.gm.spatial_name}_userpts_{self.gm.satellite_name}_{date}.csv"
            )

            print(f"Loading user points from {points_csv_file}")  # Debugging statement

            df_userpts = pd.read_csv(points_csv_file)

            df_isburned = df_userpts[df_userpts['isBurned']==1]
            df_unburned = df_userpts[df_userpts['isBurned']==0]

            points_burned = initialize_userpoints(
                point_locations=(df_isburned['x'].tolist(), df_isburned['y'].tolist()),
                color='red',marker='x',label='Burned'
                )
            
            points_unburned = initialize_userpoints(
                point_locations=(df_unburned['x'].tolist(), df_unburned['y'].tolist()),
                color='blue',marker='+',label='Unburned'
                )

            loading.value = True
            loading.visible = True
            loading.name='Loading Image...'

            try:
                polys_areamask.data = None
                rgb = self.gm.get_nir_red_hv_rgb(date=date,in_app=True,no_title=True)
                
            except Exception as e:
                pn.state.notifications.error(f"Error loading image for date {date}: {str(e)}",duration=6000)
                loading.value = False
                loading.name='Error Loading Image.'
            
            if isinstance(rgb, str):
                pn.state.notifications.error(rgb)
                return
            
            if rgb is not None:
                hv_pane.object = (rgb * points_burned * points_unburned * polys_areamask).opts(
                    title=f"{self.satellite} Imagery - {date}",
                )

            loading.value = False
            loading.visible = False

        load_img_button.on_click(
            lambda event: pn.state.notifications.info(f"Loading image for analysis date: {analysis_date_selector.value}")
        )

        load_img_button.on_click(update_hv_rgb_with_points)

        alert_pane = pn.pane.Alert(
            f"Performing one-time resampling of NLCD from full resolution to '{self.gm.spatial_name}' domain. This may take a few minutes...",
            visible=False,
            alert_type='warning',
            width=400
            )

        def categorize_pixels(event):
            date = analysis_date_selector.value
            method = method_selector.value
            pre_date = pre_fire_date_selector.value
            pre_date = None if pre_date == 'None' else pre_date

            loading.value = True
            loading.visible = True
            loading.name='Classifying Pixels...'

            # Ensure old burnmask layers are cleared before loading new results
            # burnmask_pane = pn.pane.HoloViews(county_overlay)

            pn.state.notifications.info(f"Classifying pixels for analysis date: {date}",duration=6000)

            landcover_mask_file_fullres = importlib.resources.files("fhba.app.appdata.annual_nlcd") / f"NLCD_LandMask_{self.gm.spatial_name}.tif"
            landcover_mask_file = importlib.resources.files("fhba.app.appdata.annual_nlcd") / f"NLCD_LandMask_{self.gm.spatial_name}_{self.gm.instrument}.tif"

            lcmask_exists = os.path.exists(landcover_mask_file)
            lcmask_fullres_exists = os.path.exists(landcover_mask_file_fullres)

            if not lcmask_exists:
                if not lcmask_fullres_exists:
                    pn.state.notifications.error("Landcover Mask File Not Found",duration=6000)
                    loading.value = False
                    loading.visible = False
                    return
                
                # pn.state.notifications.warning(f"Performing one-time resampling of NLCD from full resolution to '{self.gm.spatial_name}' domain.")

                loading.name='Resampling Landcover Mask (may take some time)...'
                alert_pane.visible = True

                self.gm.resample_landmask(
                    landcover_mask_file_fullres=landcover_mask_file_fullres,
                    landcover_mask_file=landcover_mask_file
                )

                loading.name='Classifying Pixels...'
                alert_pane.visible = False

            burnmask, confidence_ds = self.gm.classify_pixels(
                    method=method,
                    date=date,
                    landcover_mask_file=landcover_mask_file,
                    pre_fire_date=pre_date,
                )
            
            loading.name='Saving Temporary Results...'
            burnmask = burnmask.rio.write_crs(
                rasterio.crs.CRS.from_user_input(self.gm.satpy_area_def.proj_str)
            )

            if getattr(self.gm,"burnmask_dir",None) is None:
                config = self.registry.read_config(return_raw_config=True)

                burnmask_dir = config['paths']['burnmask_dir']
                burnmask_dir = burnmask_dir.replace("./appdata","fhba.app.appdata").split("/")[0]
                burnmask_dir = importlib.resources.files(burnmask_dir) / "burnmask"
                burnmask_dir = burnmask_dir / self.gm.start_date.split("-")[0] / self.gm.satellite_name
                burnmask_dir = str(burnmask_dir)
                self.gm.burnmask_dir = burnmask_dir
                self.registry.save_json()

            os.makedirs(self.gm.burnmask_dir,exist_ok=True)

            burnmask_tmp_file = os.path.join(
                self.gm.burnmask_dir,
                f"{self.gm.satellite_name}_{self.gm.spatial_name}_{date}_tmp.tif"
            )

            burnmask.rio.to_raster(burnmask_tmp_file)

            pn.state.notifications.info(f"Loading burnmask for date: {date}")
            loading.name='Loading Burnmask...'

            burnmask_qm = self.gm.get_burnmask_hv_rgb(burnmask_array=burnmask.burnmask)

            burnmask_pane.object = (county_overlay * (burnmask_qm * polys_areamask)).opts(
                title=f"Burn Mask - {date} ({method} classification)")

            # conf_vals = confidence_ds['confidence'].values
            # conf_da = confidence_ds['confidence']
            # conf_img = hv.Image(
            #     (conf_da.x.values, conf_da.y.values, conf_vals),
            #     kdims=['x', 'y'], vdims=['confidence']
            # ).opts(cmap='RdBu_r', alpha=0.4, colorbar=True,
            #         clim=(float(np.nanpercentile(conf_vals, 5)),
            #                 float(np.nanpercentile(conf_vals, 95))),
            #         title='Confidence (red=burned, blue=unburned)',
            #         width=500, height=1000)
            
            # hv_pane.object = hv_pane.object * conf_img

            loading.value = False
            loading.visible = False

        categorize_pixel_button.on_click(categorize_pixels)

        def export_burnmask(event):

            date = analysis_date_selector.value
            method = method_selector.value

            burnmask_tmp_file = os.path.join(
                self.gm.burnmask_dir,
                f"{self.gm.satellite_name}_{self.gm.spatial_name}_{date}_tmp.tif"
            )

            if not os.path.exists(burnmask_tmp_file):
                pn.state.notifications.error(f"No burnmask found for date {date}. Please run pixel categorization first.",duration=6000)
                return
            
            burnmask_final_file = os.path.join(
                self.gm.burnmask_dir,
                f"{self.gm.satellite_name}_{self.gm.spatial_name}_{date}_burnmask_{method_selector.value}.tif"
            )

            counties = gpd.read_file(self.gm.county_shp + ".shp")
            counties = counties.to_crs(self.gm.satpy_area_def.proj_str)

            polys_areamask_gdf = polys2gdf(poly_stream)

            with rxr.open_rasterio(burnmask_tmp_file) as ds:
                ds = ds.rio.clip(geometries=counties.geometry).fillna(0)

                if not polys_areamask_gdf.empty:

                    print("="*79)
                    print("Excluding pixels within user polygons:")
                    print(polys_areamask_gdf)
                    print("="*79)

                    for geom in polys_areamask_gdf.geometry:
                        print(geom)
                        ds = ds.rio.clip(geometries=[geom],invert=True,drop=False)

                ds.rio.to_raster(burnmask_final_file)

                if getattr(self.gm, 'burnmask_by_date', None) is None:
                    self.gm.burnmask_by_date = {}

                self.gm.burnmask_by_date[date] = burnmask_final_file
                self.gm.update_categorization_status(date, "Categorized")
                self.registry.save_json()
                pn.state.notifications.success(f"Burnmask exported successfully for date {date}.",duration=6000)

            os.remove(burnmask_tmp_file)

        export_burnmask_button.on_click(export_burnmask)

        pane = pn.Row(
            pn.Column(
                instr,
                pn.pane.Alert(
                    "Tip: If many spurious small burned pixels appear, try increasing "
                    "unburned training points near those features.",
                    sizing_mode='stretch_width', alert_type='warning', width=250),
                sizing_mode='stretch_height',
                width=250,
            ),
            pn.Column(
                pn.pane.Markdown("## Classify Pixels"),
                pn.Row(analysis_date_selector, pre_fire_date_selector,method_selector),
                method_help,
                pn.Row(load_img_button, categorize_pixel_button),
                pn.layout.Divider(),
                table,margin=(40, 10), width=600,styles={'background': '#f0f0f0'}
            ),
            pn.Column(
                pn.pane.Markdown("## Burn Scar Map"),
                export_burnmask_button,
                pn.Column(alert_pane,loading),
                pn.Row(
                    pn.Column(
                        pn.pane.Markdown(f"**Imagery**"),
                        hv_pane,
                    ),
                    pn.Column(
                        pn.pane.Markdown(f"**Burn Mask**"),
                        burnmask_pane,
                    ),
                ),
                margin=(40, 10), styles={'background': '#f0f0f0'}
            )
        )
        
        return pane
    
    def panel(self):
        return pn.Row(self.view)