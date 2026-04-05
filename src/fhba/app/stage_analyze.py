import logging
import os

import pandas as pd
import panel as pn
import param

from fhba.app.utils import get_instructions, initialize_userpoints, pts2df

logger = logging.getLogger(__name__)

# Thresholds for training-point count validation
_MIN_POINTS_ERROR   = 10   # hard block below this
_MIN_POINTS_WARNING = 30   # warning below this (but allow export)


class StageAnalyze(param.Parameterized):

    year = param.Integer()
    satellite = param.String()
    registry = param.Parameter()
    gm = param.Parameter()

    @param.depends('year', 'satellite', 'registry', 'gm')
    def table_pane(self, return_df=False):

        gm_df = self.gm.to_df().reset_index()
        gm_df = gm_df.rename(columns={'index': 'date'})
        gm_df = gm_df[gm_df['download_status'] == True]

        table = pn.pane.DataFrame(
            gm_df[['date', 'user_categorization', 'analysis_status']], index=False,
            sizing_mode='stretch_both'
        )

        if return_df:
            return table, gm_df

        return table

    @param.depends('year', 'satellite', 'registry', 'gm')
    def view(self):
        instr = get_instructions("05_instr_select_pixels.md", instr_width=250)

        table, gm_df = self.table_pane(return_df=True)

        analysis_date_selector = pn.widgets.Select(
            name="",
            options=gm_df['date'].tolist(),
            width=200,
        )

        load_img_button = pn.widgets.Button(
            name="Load Analysis Date Image", button_type="primary", width=180
        )

        show_fire_checkbox = pn.widgets.Checkbox(
            name="Show HMS Active Fire Overlay", value=True
        )
        fire_opacity_slider = pn.widgets.FloatSlider(
            name="Fire Point Opacity", start=0.0, end=1.0, step=0.05,
            value=1.0, width=180,
        )

        load_pts_button = pn.widgets.Button(
            name="Import Previous User Points", button_type="primary", width=250
        )

        hv_pane = pn.pane.HoloViews(None, width=500, height=1000)

        loading = pn.indicators.LoadingSpinner(
            name="Loading Image...", width=200, height=25, visible=False, value=False)

        points_burned,   points_burned_stream   = initialize_userpoints(color='red',  label='Burned')
        points_unburned, points_unburned_stream = initialize_userpoints(color='blue', marker='+', label='Unburned')

        def _build_composite(rgb, date):
            """Return rgb * points overlay, optionally with active fire layer."""
            composite = rgb * points_burned * points_unburned
            if show_fire_checkbox.value:
                try:
                    fire_overlay = self.gm.get_hms_active_fire_overlay(
                        date, alpha=fire_opacity_slider.value)
                    if fire_overlay is not None:
                        composite = composite * fire_overlay
                except Exception as exc:
                    logger.warning("Could not load active fire overlay: %s", exc)
            return composite

        def update_hv_rgb(event):
            date = analysis_date_selector.value

            loading.value = True
            loading.visible = True
            loading.name = 'Loading Image...'

            try:
                rgb = self.gm.get_truecolor_hv_rgb(date=date, in_app=True, include_counties=True)
                # Reset points to empty when loading a new image
                points_burned.data   = pd.DataFrame({'x': [], 'y': []})
                points_unburned.data = pd.DataFrame({'x': [], 'y': []})
            except Exception as e:
                pn.state.notifications.error(
                    f"Error loading image for date {date}: {str(e)}", duration=6000)
                loading.value = False
                loading.name = 'Error Loading Image.'
                return

            if isinstance(rgb, str):
                pn.state.notifications.error(rgb, duration=6000)
                loading.value = False
                loading.visible = False
                loading.name = 'No data available.'
                return

            if rgb is not None:
                hv_pane.object = _build_composite(rgb, date)

            loading.value = False
            loading.visible = False

        load_img_button.on_click(
            lambda event: pn.state.notifications.info(
                f"Loading image for analysis date: {analysis_date_selector.value}")
        )
        load_img_button.on_click(update_hv_rgb)

        def load_user_points(event):
            date = analysis_date_selector.value

            points_csv_file = os.path.join(
                self.gm.userpts_dir,
                f"./{self.gm.spatial_name}_userpts_{self.gm.satellite_name}_{date}.csv"
            )

            if not os.path.exists(points_csv_file):
                pn.state.notifications.error(
                    f"No previous points found for date {date}.", duration=6000)
                return

            df_userpts = pd.read_csv(points_csv_file)
            df_isburned = df_userpts[df_userpts['isBurned'] == 1]
            df_unburned = df_userpts[df_userpts['isBurned'] == 0]

            points_burned.data   = pd.DataFrame({'x': df_isburned['x'].tolist(), 'y': df_isburned['y'].tolist()})
            points_unburned.data = pd.DataFrame({'x': df_unburned['x'].tolist(), 'y': df_unburned['y'].tolist()})

            loading.value = True
            loading.visible = True
            loading.name = 'Loading Image...'

            try:
                rgb = self.gm.get_truecolor_hv_rgb(date=date, in_app=True, include_counties=True)
            except Exception as e:
                pn.state.notifications.error(
                    f"Error loading image for date {date}: {str(e)}", duration=6000)
                loading.value = False
                loading.name = 'Error Loading Image.'
                return

            if isinstance(rgb, str):
                pn.state.notifications.error(rgb, duration=6000)
                loading.value = False
                loading.visible = False
                loading.name = 'No data available.'
                return

            if rgb is not None:
                hv_pane.object = _build_composite(rgb, date)

            loading.value = False
            loading.visible = False

        load_pts_button.on_click(load_user_points)

        export_button = pn.widgets.Button(name='Export Points', button_type='danger')

        export_options = pn.widgets.CheckBoxGroup(
            name='Export Options',
            value=[],
            options=['Overwrite Points']
        )

        def export_button_callback(event):
            selected_options = export_options.value
            date = analysis_date_selector.value

            export_df = pts2df(
                burned_pts=points_burned_stream.data,
                unburned_pts=points_unburned_stream.data
            )

            if export_df.empty:
                pn.state.notifications.error(
                    'No points have been selected for this granule.', duration=3000)
                return

            n_burned   = int((export_df['isBurned'] == 1).sum())
            n_unburned = int((export_df['isBurned'] == 0).sum())

            # ── Validation: hard block if either class is too small ────────────
            if n_burned < _MIN_POINTS_ERROR:
                pn.state.notifications.error(
                    f'Need at least {_MIN_POINTS_ERROR} burned points (currently {n_burned}). '
                    f'Add more burned training points before exporting.',
                    duration=6000)
                return

            if n_unburned < _MIN_POINTS_ERROR:
                pn.state.notifications.error(
                    f'Need at least {_MIN_POINTS_ERROR} unburned points (currently {n_unburned}). '
                    f'Add more unburned training points before exporting.',
                    duration=6000)
                return

            # ── Warning: recommend more points ────────────────────────────────
            if n_burned < _MIN_POINTS_WARNING:
                pn.state.notifications.warning(
                    f'Only {n_burned} burned points selected (recommended ≥ {_MIN_POINTS_WARNING}). '
                    f'Classification accuracy may be reduced.',
                    duration=6000)

            if n_unburned < _MIN_POINTS_WARNING:
                pn.state.notifications.warning(
                    f'Only {n_unburned} unburned points selected (recommended ≥ {_MIN_POINTS_WARNING}). '
                    f'Classification accuracy may be reduced.',
                    duration=6000)

            if self.gm.userpts_dir is None:
                self.gm.userpts_dir = "./tmp_userpts"

            os.makedirs(self.gm.userpts_dir, exist_ok=True)

            points_csv_file = os.path.join(
                self.gm.userpts_dir,
                f"./{self.gm.spatial_name}_userpts_{self.gm.satellite_name}_{date}.csv"
            )

            proceed = True
            if os.path.exists(points_csv_file):
                proceed = 'Overwrite Points' in selected_options

            if proceed:
                export_df.to_csv(points_csv_file, index=False)
                pn.state.notifications.success(
                    f'Points exported ({n_burned} burned, {n_unburned} unburned).', duration=3000)

                self.gm.update_analysis_status(
                    date, f'{n_burned} burned points, {n_unburned} unburned points')

                if getattr(self.gm, 'userpts_by_date', None) is None:
                    self.gm.userpts_by_date = {}
                self.gm.userpts_by_date[date] = points_csv_file
                self.registry.save_json()

            else:
                pn.state.notifications.error(
                    'Points already exported for this granule. '
                    'Check "Overwrite Points" to replace.',
                    duration=3000)

        def reload_table(event):
            table.object = self.table_pane(return_df=False).object

        export_button.on_click(export_button_callback)
        export_button.on_click(reload_table)

        return pn.Row(
            pn.Column(
                instr,
                pn.pane.Alert(
                    f"Select at least {_MIN_POINTS_WARNING} burned and "
                    f"{_MIN_POINTS_WARNING} unburned points from a variety of locations "
                    f"for best classification accuracy. Minimum required: {_MIN_POINTS_ERROR} each.",
                    sizing_mode='stretch_width', alert_type='warning', width=250),
                sizing_mode='stretch_height',
                width=250,
            ),
            pn.Column(
                pn.pane.Markdown("## Select Analysis Date from Table of Downloaded Granules"),
                pn.Row(
                    analysis_date_selector,
                    load_img_button,
                    loading
                ),
                pn.Row(show_fire_checkbox, fire_opacity_slider),
                pn.layout.Divider(),
                table,
                width=600,
                sizing_mode='stretch_height'
            ),
            pn.Column(
                pn.pane.Markdown("## Select Pixels"),
                pn.Row(load_pts_button, export_button, export_options),
                hv_pane,
                margin=(40, 40), styles={'background': '#f0f0f0'},
                sizing_mode='stretch_both'
            ))

    def panel(self):
        return pn.Row(self.view,)
