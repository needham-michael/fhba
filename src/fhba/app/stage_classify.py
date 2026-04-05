import logging
import os
import importlib

import geopandas as gpd
import holoviews as hv
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

    @param.depends('year', 'satellite', 'registry', 'gm')
    def table_pane(self, return_df=False):

        gm_df = self.gm.to_df().reset_index()
        gm_df = gm_df.rename(columns={'index': 'date'})
        gm_df = gm_df[gm_df['analysis_status'] != "Unanalyzed"]

        table = pn.pane.DataFrame(
            gm_df[['date', 'analysis_status', 'categorization_status']], index=False,
            width=600
        )

        if return_df:
            return table, gm_df

        return table

    @param.depends('year', 'satellite', 'registry', 'gm')
    def view(self):
        instr = get_instructions("06_eucl_categ.md", instr_width=250)

        table, gm_df = self.table_pane(return_df=True)

        analysis_date_selector = pn.widgets.Select(
            name="Post-fire Analysis Date",
            options=gm_df['date'].tolist(),
            width=200,
        )

        # Pre-fire reference date for dNBR computation
        clear_dates = self.gm.get_clear_processed_dates()
        pre_fire_date_selector = pn.widgets.Select(
            name="Pre-fire Reference Date (for dNBR)",
            options=['None'] + clear_dates,
            value='None',
            width=220,
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

        # Active Fire overlay checkbox — on by default so fire pixels are
        # visible while placing burned/unburned training points.
        show_fire_checkbox = pn.widgets.Checkbox(
            name="Show VIIRS Active Fire Overlay", value=True
        )
        fire_opacity_slider = pn.widgets.FloatSlider(
            name="Fire Point Opacity", start=0.0, end=1.0, step=0.05,
            value=1.0, width=180,
        )

        load_img_button = pn.widgets.Button(
            name="Load Analysis Date Image", button_type="primary", width=180
        )

        categorize_pixel_button = pn.widgets.Button(
            name="Classify Pixels", button_type="primary", width=150
        )

        batch_button = pn.widgets.Button(
            name="Batch Classify All Dates", button_type="warning", width=180
        )

        export_burnmask_button = pn.widgets.Button(
            name="Export Burnmask", button_type="primary", width=150
        )

        county_overlay = self.gm.get_county_overlay().opts(width=500, height=1000)
        hv_pane = pn.pane.HoloViews(county_overlay)

        # Second pane for pre-fire / side-by-side comparison
        pre_fire_pane = pn.pane.HoloViews(None, width=500, height=1000, visible=False)

        loading = pn.indicators.LoadingSpinner(
            name="Loading Image...", width=200, height=50, visible=False, value=False)

        polys_areamask, poly_stream = initialize_userpolys()

        # Mutable container so both callbacks can read/write the current
        # fire overlay without a global variable.
        _fire_overlay = [None]

        alert_pane = pn.pane.Alert(
            f"Performing one-time resampling of NLCD to '{self.gm.spatial_name}' domain. "
            f"This may take a few minutes...",
            visible=False, alert_type='warning', width=400)

        # Area statistics output
        stats_md = pn.pane.Markdown("", width=500)
        stats_table = pn.pane.DataFrame(pd.DataFrame(), index=False, width=500)

        def update_hv_rgb_with_points(event):
            date = analysis_date_selector.value

            points_csv_file = os.path.join(
                self.gm.userpts_dir,
                f"./{self.gm.spatial_name}_userpts_{self.gm.satellite_name}_{date}.csv"
            )

            df_userpts = pd.read_csv(points_csv_file)
            df_isburned = df_userpts[df_userpts['isBurned'] == 1]
            df_unburned = df_userpts[df_userpts['isBurned'] == 0]

            points_burned = initialize_userpoints(
                point_locations=(df_isburned['x'].tolist(), df_isburned['y'].tolist()),
                color='red', marker='x', label='Burned')

            points_unburned = initialize_userpoints(
                point_locations=(df_unburned['x'].tolist(), df_unburned['y'].tolist()),
                color='blue', marker='+', label='Unburned')

            loading.value = True
            loading.visible = True
            loading.name = 'Loading Image...'

            try:
                polys_areamask.data = None
                rgb = self.gm.get_truecolor_hv_rgb(date=date, in_app=True, no_title=True)

            except Exception as e:
                pn.state.notifications.error(
                    f"Error loading image for date {date}: {str(e)}", duration=6000)
                loading.value = False
                loading.name = 'Error Loading Image.'
                return

            if isinstance(rgb, str):
                pn.state.notifications.error(rgb)
                return

            # ── Active Fire overlay ────────────────────────────────────────────
            fire_overlay = None
            if show_fire_checkbox.value:
                try:
                    fire_overlay = self.gm.get_hms_active_fire_overlay(
                        date, alpha=fire_opacity_slider.value)
                except Exception as exc:
                    logger.warning("Could not load active fire overlay: %s", exc)
            # Remember the overlay so the classify callback can re-apply it.
            _fire_overlay[0] = fire_overlay

            composite = rgb * points_burned * points_unburned * polys_areamask
            if fire_overlay is not None:
                composite = composite * fire_overlay
            hv_pane.object = composite

            # ── Side-by-side pre-fire comparison ──────────────────────────────
            pre_date = pre_fire_date_selector.value
            if pre_date and pre_date != 'None':
                try:
                    pre_rgb = self.gm.get_truecolor_hv_rgb(date=pre_date, in_app=True, no_title=True)
                    if pre_rgb and not isinstance(pre_rgb, str):
                        pre_fire_pane.object = pre_rgb
                        pre_fire_pane.visible = True
                    else:
                        pre_fire_pane.visible = False
                except Exception as exc:
                    logger.warning("Could not load pre-fire image for %s: %s", pre_date, exc)
                    pre_fire_pane.visible = False
            else:
                pre_fire_pane.visible = False

            loading.value = False
            loading.visible = False

        load_img_button.on_click(
            lambda event: pn.state.notifications.info(
                f"Loading image for analysis date: {analysis_date_selector.value}")
        )
        load_img_button.on_click(update_hv_rgb_with_points)

        def _resolve_landmask():
            """Return (landmask_full_res_path, landmask_resampled_path)."""
            lm_full = importlib.resources.files("fhba.app.appdata.annual_nlcd") / \
                f"NLCD_LandMask_{self.gm.spatial_name}.tif"
            lm_resampled = importlib.resources.files("fhba.app.appdata.annual_nlcd") / \
                f"NLCD_LandMask_{self.gm.spatial_name}_{self.gm.instrument}.tif"
            return lm_full, lm_resampled

        def _ensure_landmask():
            """Ensure the resampled landmask exists; resample if needed.  Returns path or None."""
            lm_full, lm_resampled = _resolve_landmask()

            if os.path.exists(lm_resampled):
                return lm_resampled

            if not os.path.exists(lm_full):
                print(f"Landcover mask file not found at expected location: {lm_full}")
                pn.state.notifications.error("Landcover mask file not found.", duration=6000)
                return None

            loading.name = 'Resampling Landcover Mask (may take some time)...'
            alert_pane.visible = True
            self.gm.resample_landmask(
                landcover_mask_file_fullres=lm_full,
                landcover_mask_file=lm_resampled)
            alert_pane.visible = False
            return lm_resampled

        def _ensure_burnmask_dir():
            if getattr(self.gm, "burnmask_dir", None) is None:
                config = self.registry.read_config(return_raw_config=True)
                bm_dir = config['paths']['burnmask_dir']
                bm_dir = bm_dir.replace("./appdata", "fhba.app.appdata").split("/")[0]
                bm_dir = importlib.resources.files(bm_dir) / "burnmask"
                bm_dir = bm_dir / self.gm.start_date.split("-")[0] / self.gm.satellite_name
                self.gm.burnmask_dir = str(bm_dir)
                self.registry.save_json()
            os.makedirs(self.gm.burnmask_dir, exist_ok=True)

        def categorize_pixels(event):
            date = analysis_date_selector.value
            method = method_selector.value
            pre_date = pre_fire_date_selector.value
            pre_date = None if pre_date == 'None' else pre_date

            loading.value = True
            loading.visible = True
            loading.name = 'Classifying Pixels...'

            pn.state.notifications.info(
                f"Classifying pixels for {date} using method='{method}'", duration=4000)

            landcover_mask_file = _ensure_landmask()
            if landcover_mask_file is None:
                loading.value = False
                loading.visible = False
                return

            try:
                burnmask, confidence_ds = self.gm.classify_pixels(
                    method=method,
                    date=date,
                    landcover_mask_file=landcover_mask_file,
                    pre_fire_date=pre_date,
                )
            except Exception as exc:
                logger.exception("Classification failed for date %s method %s", date, method)
                pn.state.notifications.error(f"Classification error: {exc}", duration=8000)
                loading.value = False
                loading.visible = False
                return

            loading.name = 'Saving Temporary Results...'
            burnmask = burnmask.rio.write_crs(
                rasterio.crs.CRS.from_user_input(self.gm.satpy_area_def.proj_str))

            _ensure_burnmask_dir()

            burnmask_tmp_file = os.path.join(
                self.gm.burnmask_dir,
                f"{self.gm.satellite_name}_{self.gm.spatial_name}_{date}_tmp.tif")
            burnmask.rio.to_raster(burnmask_tmp_file)

            pn.state.notifications.info(f"Loading burnmask for date: {date}")
            loading.name = 'Loading Burnmask...'

            # Replace the pane with just the burn mask + draw tool so the
            # complex overlay accumulation can't break HoloViews rendering.
            burnmask_qm = self.gm.get_burnmask_hv_rgb(burnmask_array=burnmask.burnmask)
            burnmask_view = burnmask_qm * polys_areamask
            # Re-apply the fire overlay if it was visible before classification.
            if _fire_overlay[0] is not None:
                burnmask_view = burnmask_view * _fire_overlay[0]
            hv_pane.object = burnmask_view

            # ── Confidence overlay (diverging colour — positive = more burned) ──
            try:
                conf_vals = confidence_ds['confidence'].values
                conf_da = confidence_ds['confidence']
                conf_img = hv.Image(
                    (conf_da.x.values, conf_da.y.values, conf_vals),
                    kdims=['x', 'y'], vdims=['confidence']
                ).opts(cmap='RdBu_r', alpha=0.4, colorbar=True,
                       clim=(float(np.nanpercentile(conf_vals, 5)),
                             float(np.nanpercentile(conf_vals, 95))),
                       title='Confidence (red=burned, blue=unburned)',
                       width=500, height=1000)
                hv_pane.object = hv_pane.object * conf_img
            except Exception as exc:
                logger.warning("Could not render confidence overlay: %s", exc)

            loading.value = False
            loading.visible = False

        categorize_pixel_button.on_click(categorize_pixels)

        def batch_classify(event):
            method = method_selector.value
            loading.value = True
            loading.visible = True
            loading.name = f'Batch classifying all dates with {method}...'
            pn.state.notifications.info(
                f"Batch classifying all analyzed dates with method='{method}'", duration=4000)

            landcover_mask_file = _ensure_landmask()
            if landcover_mask_file is None:
                loading.value = False
                loading.visible = False
                return

            _ensure_burnmask_dir()

            try:
                self.gm.classify_pixels_date_range(method=method)
                pn.state.notifications.success("Batch classification complete.", duration=5000)
                table.object = self.table_pane(return_df=False).object
            except Exception as exc:
                pn.state.notifications.error(f"Batch classification error: {exc}", duration=8000)

            loading.value = False
            loading.visible = False

        batch_button.on_click(batch_classify)

        def export_burnmask(event):
            date = analysis_date_selector.value

            burnmask_tmp_file = os.path.join(
                self.gm.burnmask_dir,
                f"{self.gm.satellite_name}_{self.gm.spatial_name}_{date}_tmp.tif")

            if not os.path.exists(burnmask_tmp_file):
                pn.state.notifications.error(
                    f"No burnmask found for date {date}. Run pixel classification first.",
                    duration=6000)
                return

            burnmask_final_file = os.path.join(
                self.gm.burnmask_dir,
                f"{self.gm.satellite_name}_{self.gm.spatial_name}_{date}_burnmask.tif")

            counties = gpd.read_file(self.gm.county_shp + ".shp")
            counties = counties.to_crs(self.gm.satpy_area_def.proj_str)
            polys_areamask_gdf = polys2gdf(poly_stream)

            with rxr.open_rasterio(burnmask_tmp_file) as ds:
                ds = ds.rio.clip(geometries=counties.geometry).fillna(0)

                if not polys_areamask_gdf.empty:
                    for geom in polys_areamask_gdf.geometry:
                        ds = ds.rio.clip(geometries=[geom], invert=True, drop=False)

                ds.rio.to_raster(burnmask_final_file)

            if getattr(self.gm, 'burnmask_by_date', None) is None:
                self.gm.burnmask_by_date = {}
            self.gm.burnmask_by_date[date] = burnmask_final_file
            self.gm.update_categorization_status(date, "Categorized")
            self.registry.save_json()
            pn.state.notifications.success(
                f"Burnmask exported for date {date}.", duration=6000)

            os.remove(burnmask_tmp_file)

            # ── Per-county area statistics ──────────────────────────────────────
            try:
                gdf_stats = self.gm.compute_burn_area_by_county(burnmask_final_file)
                # Include both UTM and EPSG:5070 columns for comparison
                columns = ['county_name', 'burned_area_km2_utm', 'burned_area_acres_utm', 'burned_area_km2_5070', 'burned_area_acres_5070']
                stats_df = gdf_stats[gdf_stats['county_name'] != 'Total'][columns].sort_values('burned_area_acres_utm', ascending=False)
                # Add the Total row back for display
                total_row = gdf_stats[gdf_stats['county_name'] == 'Total'][columns]
                stats_df = pd.concat([stats_df, total_row], ignore_index=True)
                stats_table.object = stats_df
                total_acres = gdf_stats.loc[gdf_stats['county_name'] == 'Total', 'burned_area_acres_utm'].iloc[0]
                total_km2   = gdf_stats.loc[gdf_stats['county_name'] == 'Total', 'burned_area_km2_utm'].iloc[0]
                stats_md.object = (
                    f"### Burned Area for {date}\n"
                    f"**Total: {total_km2:.1f} km²  ({total_acres:,.0f} acres)**"
                )
            except Exception as exc:
                logger.warning("Could not compute area statistics: %s", exc)

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
                pn.Row(analysis_date_selector, pre_fire_date_selector),
                pn.Row(method_selector, show_fire_checkbox, fire_opacity_slider),
                method_help,
                pn.Row(load_img_button, categorize_pixel_button, batch_button),
                pn.layout.Divider(),
                table,
                margin=(40, 10), width=650, styles={'background': '#f0f0f0'}
            ),
            pn.Column(
                pn.pane.Markdown("## Burn Scar Map"),
                export_burnmask_button,
                pn.Column(alert_pane, loading),
                pn.Row(
                    pn.Column(
                        pn.pane.Markdown("**Pre-fire image**"),
                        pre_fire_pane,
                    ),
                    pn.Column(
                        pn.pane.Markdown("**Post-fire / burn mask**"),
                        hv_pane,
                    ),
                ),
                pn.layout.Divider(),
                stats_md,
                stats_table,
                margin=(40, 10), styles={'background': '#f0f0f0'}
            )
        )

        return pane

    def panel(self):
        return pn.Row(self.view,)
