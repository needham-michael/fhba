import os
import re

import pandas as pd
import panel as pn
import param

from fhba.app.utils import get_instructions


class StageAggregate(param.Parameterized):

    year = param.Integer()
    satellite = param.String()
    registry = param.Parameter()
    gm = param.Parameter()

    def get_widgets(self):

        generate_button = pn.widgets.Button(
            name="Generate Seasonal Burn Map",
            button_type="primary",
            width=220,
            # disabled=True
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

         # Classification method selector
        method_selector = pn.widgets.Select(
            name="Classification Method",
            options=['eucl', 'rf', 'svm'],
            value='eucl',
            width=120,
        )

        ytd_selector = pn.widgets.DatePicker(
            name='YTD Cutoff Date',
            enabled_dates=list(pd.date_range(
                start=f"{self.year}-02-15", 
                end=f"{self.year}-05-15",
                freq='D'
            ).strftime("%Y-%m-%d")),
        )

        return generate_button, compute_stats_button, download_stats_button, loading, method_selector,ytd_selector

    @param.depends('year', 'satellite', 'registry', 'gm')
    def view(self):

        instr = get_instructions("07_instr_aggregate.md", instr_width=250)

        
        # -- Status table -------------------------------------------------------
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

        # -- Controls -----------------------------------------------------------
        generate_button, compute_stats_button, download_stats_button, loading, method_selector, ytd_selector = self.get_widgets()


        result_md = pn.pane.Markdown("", width=600)
        stats_table = pn.pane.DataFrame(pd.DataFrame(), index=False, width=600)

        # Holds the seasonal burnmask path once generated
        _state = {'seasonal_file': None, 'stats_csv': None}

        def generate_seasonal(event):
            loading.value = True
            loading.visible = True
            result_md.object = ""

            try:
                method = method_selector.value
                seasonal_file = self.gm.aggregate_burnmasks(
                    method=method,
                    ytd_cutoff=str(ytd_selector.value)
                    )
                _state['seasonal_file'] = seasonal_file
                result_md.object = (
                    f"✅ Seasonal burn map saved to:\n\n`{seasonal_file}`"
                )
                compute_stats_button.disabled = False
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

            file_date = re.search(r'(\d{4}-\d{2}-\d{2})', _state['seasonal_file'])
            date_str = file_date.group(1) if file_date else "unknown date"

            try:
                gdf = self.gm.compute_burn_area_by_county(_state['seasonal_file'])

                print(f"== Burned area by county through {date_str} ==")
                print(f"{gdf =}")


                columns = ['county_name', 'burned_area_km2', 'burned_area_acres']
                stats_df = gdf[gdf['county_name'] != 'Total'][columns].copy()
                stats_df = stats_df.sort_values('burned_area_acres', ascending=False)
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

                total_km2 = gdf.loc[gdf['county_name'] == 'Total', 'burned_area_km2'].iloc[0]
                total_acres = sgdf.loc[gdf['county_name'] == 'Total', 'burned_area_acres'].iloc[0]
                result_md.object += (
                    f"\n\n**Total burned area (through {date_str}):** {total_acres:,.0f} acres  "
                    f"({total_km2:.1f} km²)"
                )

                pn.state.notifications.success("County statistics computed.", duration=5000)

            except Exception as exc:
                pn.state.notifications.error(str(exc), duration=8000)

            finally:
                loading.value = False
                loading.visible = False

        generate_button.on_click(generate_seasonal)
        compute_stats_button.on_click(compute_stats)

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
                pn.Column(
                    pn.Row(ytd_selector,method_selector),
                    pn.Row(generate_button, compute_stats_button),
                ),
                loading,
                result_md,
                pn.pane.Markdown(f"### Burned Area by County)"),
                stats_table,
                download_stats_button,
                margin=(40, 10), sizing_mode='stretch_both',
                styles={'background': '#f0f0f0'}
            )
        )

        return pane

    def panel(self):
        return pn.Row(self.view,)