"""Stage 7 — Aggregate Burn Scars.

Unions all finalized burn masks for the selected year/satellite into a single
seasonal burn map and reports per-county burned area statistics.
"""
import os

import holoviews as hv
import numpy as np
import pandas as pd
import panel as pn
import param

from fhba.app.utils import get_instructions

pn.extension('holoviews')


class StageAggregate(param.Parameterized):

    year = param.Integer()
    satellite = param.String()
    registry = param.Parameter()
    gm = param.Parameter()

    @param.depends('year', 'satellite', 'registry', 'gm')
    def view(self):

        instr = get_instructions("07_instr_aggregate.md", instr_width=250)

        # ── Status table ───────────────────────────────────────────────────────
        gm_df = self.gm.to_df().reset_index()
        gm_df = gm_df.rename(columns={'index': 'date'})
        categorized = gm_df[gm_df['categorization_status'] == 'Categorized']
        n_ready = len(categorized)

        status_md = pn.pane.Markdown(
            f"**{n_ready} burn mask(s) ready for aggregation.**\n\n" +
            ("No burn masks found. Please complete Stage 6 for at least one date first."
             if n_ready == 0 else ""),
            width=600
        )

        table = pn.pane.DataFrame(
            categorized[['date', 'categorization_status']].reset_index(drop=True),
            index=False,
            width=400
        )

        # ── Controls ───────────────────────────────────────────────────────────
        generate_button = pn.widgets.Button(
            name="Generate Seasonal Burn Map",
            button_type="primary",
            width=220,
            disabled=(n_ready == 0)
        )

        compute_stats_button = pn.widgets.Button(
            name="Compute County Area Statistics",
            button_type="default",
            width=220,
            disabled=True
        )

        download_stats_button = pn.widgets.FileDownload(
            label="Download Statistics CSV",
            button_type="success",
            width=220,
            visible=False
        )

        loading = pn.indicators.LoadingSpinner(
            name="Processing...", width=200, height=50,
            visible=False, value=False
        )

        result_md = pn.pane.Markdown("", width=600)
        stats_table = pn.pane.DataFrame(pd.DataFrame(), index=False, width=600)

        # Holds the seasonal burnmask path once generated
        _state = {'seasonal_file': None, 'stats_csv': None}

        # Map pane — pre-load if a seasonal file already exists
        hv_pane = pn.pane.HoloViews(None, width=500, height=1000)
        _seasonal_path = os.path.join(
            self.gm.burnmask_dir,
            f"{self.gm.satellite_name}_{self.gm.spatial_name}_"
            f"{self.gm.start_date.split('-')[0]}_seasonal_burnmask.tif"
        )
        if os.path.exists(_seasonal_path):
            try:
                hv_pane.object = self.gm.get_burnmask_hv_rgb(burnmask_file=_seasonal_path)
                _state['seasonal_file'] = _seasonal_path
                compute_stats_button.disabled = False
            except Exception:
                pass

        def _render_map(seasonal_file):
            try:
                hv_pane.object = self.gm.get_burnmask_hv_rgb(burnmask_file=seasonal_file)
            except Exception as exc:
                pn.state.notifications.warning(f"Could not render map: {exc}", duration=6000)

        def generate_seasonal(event):
            loading.value = True
            loading.visible = True
            result_md.object = ""

            try:
                seasonal_file = self.gm.aggregate_burnmasks()
                _state['seasonal_file'] = seasonal_file
                result_md.object = (
                    f"✅ Seasonal burn map saved to:\n\n`{seasonal_file}`"
                )
                compute_stats_button.disabled = False
                _render_map(seasonal_file)
                pn.state.notifications.success(
                    "Seasonal burn map generated successfully.", duration=5000)

            except Exception as exc:
                result_md.object = f"❌ Error: {exc}"
                pn.state.notifications.error(str(exc), duration=8000)

            finally:
                loading.value = False
                loading.visible = False

        def compute_stats(event):
            if _state['seasonal_file'] is None:
                pn.state.notifications.warning("Generate the seasonal burn map first.")
                return

            loading.value = True
            loading.visible = True

            try:
                gdf = self.gm.compute_burn_area_by_county(_state['seasonal_file'])

                # Include both UTM and EPSG:5070 columns for comparison
                columns = ['county_name', 'burned_area_km2_utm', 'burned_area_acres_utm', 'burned_area_km2_5070', 'burned_area_acres_5070']
                stats_df = gdf[gdf['county_name'] != 'Total'][columns].copy()
                stats_df = stats_df.sort_values('burned_area_acres_utm', ascending=False)
                # Add the Total row back for display
                total_row = gdf[gdf['county_name'] == 'Total'][columns]
                stats_df = pd.concat([stats_df, total_row], ignore_index=True)
                stats_table.object = stats_df

                # Write CSV to a temp location for download
                csv_path = os.path.join(
                    self.gm.burnmask_dir,
                    f"{self.gm.satellite_name}_{self.gm.spatial_name}_"
                    f"{self.gm.start_date.split('-')[0]}_burn_area_by_county.csv"
                )
                stats_df.to_csv(csv_path, index=False)
                _state['stats_csv'] = csv_path

                download_stats_button.file = csv_path
                download_stats_button.filename = os.path.basename(csv_path)
                download_stats_button.visible = True

                total_km2 = gdf.loc[gdf['county_name'] == 'Total', 'burned_area_km2_utm'].iloc[0]
                total_acres = stats_df['burned_area_acres'].sum()
                result_md.object += (
                    f"\n\n**Total burned area:** {total_km2:.1f} km²  "
                    f"({total_acres:,.0f} acres)"
                )

                pn.state.notifications.success("County statistics computed.", duration=5000)

            except Exception as exc:
                pn.state.notifications.error(str(exc), duration=8000)

            finally:
                loading.value = False
                loading.visible = False

        generate_button.on_click(generate_seasonal)
        compute_stats_button.on_click(compute_stats)

        # ── Date-level truecolor + burn mask viewer ────────────────────────────
        _processed_dates = sorted([
            d for d in self.gm.processed_granules_by_date
            if self.gm.processed_granules_by_date.get(d)
            and os.path.exists(self.gm.processed_granules_by_date[d])
        ])
        date_selector = pn.widgets.Select(
            name="Date", options=_processed_dates,
            value=_processed_dates[0] if _processed_dates else None,
            width=180,
        )
        opacity_slider = pn.widgets.FloatSlider(
            name="Burn Mask Opacity", start=0.0, end=1.0, step=0.05,
            value=0.7, width=180
        )
        view_date_button = pn.widgets.Button(
            name="View Date", button_type="default", width=120)
        date_hv_pane = pn.pane.HoloViews(None, width=500, height=1000)

        def _render_date_view(event=None):
            date = date_selector.value
            if date is None:
                return
            burnmask_file = (self.gm.burnmasks_by_date or {}).get(date)
            try:
                date_hv_pane.object = self.gm.get_truecolor_burnmask_hv(
                    date=date, burnmask_file=burnmask_file,
                    burnmask_opacity=opacity_slider.value)
            except Exception as exc:
                pn.state.notifications.warning(f"Could not render date view: {exc}", duration=6000)

        view_date_button.on_click(_render_date_view)
        opacity_slider.param.watch(_render_date_view, 'value')

        pane = pn.Row(
            pn.Column(
                instr,
                pn.pane.Alert(
                    "Aggregation unions all finalized burn masks for this year/satellite "
                    "into a single seasonal map.",
                    alert_type='info', width=250
                ),
                sizing_mode='stretch_height',
                width=250,
            ),
            pn.Column(
                pn.pane.Markdown("## Seasonal Burn Scar Aggregation"),
                status_md,
                table,
                pn.layout.Divider(),
                pn.Row(generate_button, compute_stats_button),
                loading,
                result_md,
                pn.pane.Markdown("### Burned Area by County"),
                stats_table,
                download_stats_button,
                margin=(40, 10), sizing_mode='stretch_both',
                styles={'background': '#f0f0f0'}
            ),
            pn.Column(
                pn.pane.Markdown("## Seasonal Burn Map"),
                hv_pane,
                margin=(40, 10),
            ),
            pn.Column(
                pn.pane.Markdown("## Date View — Truecolor + Burn Mask"),
                pn.Row(date_selector, opacity_slider, view_date_button),
                date_hv_pane,
                margin=(40, 10),
            ),
        )

        return pane

    def panel(self):
        return pn.Row(self.view,)
