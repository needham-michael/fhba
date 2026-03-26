import os

import pandas as pd
import panel as pn
import param

from fhba.app.utils import get_instructions, initialize_userpoints, pts2df

class StageAnalyze(param.Parameterized):

    year = param.Integer()
    satellite = param.String()
    registry = param.Parameter()
    gm = param.Parameter()

    @param.depends('year','satellite','registry', 'gm')
    def table_pane(self,return_df=False):

        gm_df = self.gm.to_df().reset_index()
        gm_df = gm_df.rename(columns={'index':'date'})
        gm_df = gm_df[gm_df['download_status'] == True]

        table = pn.pane.DataFrame(
            gm_df[['date','user_categorization','analysis_status']], index=False, sizing_mode='stretch_both'
        )

        if return_df:
            return table, gm_df
        
        return table

    @param.depends('year','satellite','registry', 'gm')
    def view(self):
        instr = get_instructions("05_instr_select_pixels.md",instr_width=250)

        table, gm_df = self.table_pane(return_df=True)

        analysis_date_selector = pn.widgets.Select(
            name="",
            options=gm_df['date'].tolist(),
            width=200,
        )

        load_img_button = pn.widgets.Button(
            name="Load Analysis Date Image", button_type="primary", width=150
        )

        load_pts_button = pn.widgets.Button(
            name="Import Previous User Points", button_type="primary", width=250
        )

        # NOTE: The image loading and analysis functionality is not yet implemented. The button click will simply trigger a notification for now.
        # FOR 2/12/2026 NEED TO IMPLEMENT: On button click, load the image for the selected analysis date and display it in a new pane below the table. This will likely involve using the load_scene and get_nir_red_img functions from hv_rgb.py to load the appropriate granule, preprocess it, and generate a preview image for analysis.   
        # THIS SHOULD UTILIZE THE load_scene and get_nir_red_img functions from hv_rgb.py to load the appropriate granule, preprocess it, and generate a preview image for analysis. The image pane should be displayed below the table and should update based on the selected analysis date when the button is clicked.
        
        # county_overlay = hv.Path(gpd.read_file(self.gm.county_shp + ".shp").geometry).opts(
        #     color='white', line_width=0.75
        #     )


        # hv_rgb = self.gm.get_nir_red_hv_rgb(date=analysis_date_selector.value)
        hv_pane = pn.pane.HoloViews(None, width=500, height=1000)

        loading = pn.indicators.LoadingSpinner(name="Loading Image...", width=200, height=25,visible=False,value=False)

        points_burned, points_burned_stream = initialize_userpoints(color='red',label='Burned')
        points_unburned, points_unburned_stream = initialize_userpoints(color='blue',marker='+',label='Unburned')

        def update_hv_rgb(event):
            date = analysis_date_selector.value

            print(f"Updating HV RGB with {date = }")  # Debugging statement
            
            loading.value = True
            loading.visible = True
            loading.name='Loading Image...'

            try:
                rgb = self.gm.get_nir_red_hv_rgb(date=date,in_app=True,include_counties=True)

                # Reset points to empty when loading a new image
                points_burned.data = pd.DataFrame({'x':[],'y':[]})
                points_unburned.data = pd.DataFrame({'x':[],'y':[]})
            except Exception as e:
                pn.state.notifications.error(f"Error loading image for date {date}: {str(e)}",duration=6000)
                loading.value = False
                loading.name='Error Loading Image.'
            
            if isinstance(rgb, str):
                pn.state.notifications.error(rgb)
                return
            
            if rgb is not None:
                hv_pane.object = rgb * points_burned * points_unburned

                # Rename the two point draw tools
                 
            loading.value = False
            loading.visible = False
        
        load_img_button.on_click(
            lambda event: pn.state.notifications.info(f"Loading image for analysis date: {analysis_date_selector.value}")
        )

        load_img_button.on_click(update_hv_rgb)

        def load_user_points(event):

            date = analysis_date_selector.value

            print(f"Updating HV RGB with {date = }")  # Debugging statement

            points_csv_file = os.path.join(
                self.gm.userpts_dir,f"./{self.gm.spatial_name}_userpts_{self.gm.satellite_name}_{date}.csv"
            )

            if not os.path.exists(points_csv_file):
                pn.state.notifications.error(f"No previous points found for date {date}.",duration=6000)
                return

            print(f"Loading user points from {points_csv_file}")  # Debugging statement

            df_userpts = pd.read_csv(points_csv_file)

            df_isburned = df_userpts[df_userpts['isBurned']==1]
            df_unburned = df_userpts[df_userpts['isBurned']==0]

            points_burned.data = pd.DataFrame({'x':df_isburned['x'].tolist(),'y':df_isburned['y'].tolist()})
            points_unburned.data = pd.DataFrame({'x':df_unburned['x'].tolist(),'y':df_unburned['y'].tolist()})

            loading.value = True
            loading.visible = True
            loading.name='Loading Image...'

            try:
                rgb = self.gm.get_nir_red_hv_rgb(date=date,in_app=True,include_counties=True)
            except Exception as e:
                pn.state.notifications.error(f"Error loading image for date {date}: {str(e)}",duration=6000)
                loading.value = False
                loading.name='Error Loading Image.'
            
            if isinstance(rgb, str):
                pn.state.notifications.error(rgb)
                return
            
            if rgb is not None:
                hv_pane.object = rgb * points_burned * points_unburned 
            loading.value = False
            loading.visible = False

        # load_pts_button.on_click(
        #     lambda event: pn.state.notifications.info(f"Importing previous user points for analysis date: {analysis_date_selector.value}")
        # )

        load_pts_button.on_click(load_user_points)

        export_button = pn.widgets.Button(
            name='Export Points', button_type='danger')

        export_options = pn.widgets.CheckBoxGroup(
            name='Export Options', 
            value=[], 
            options=['Overwrite Points']
        )

        def export_button_callback(event):
            selected_options = export_options.value
            date = analysis_date_selector.value

            points_csv_file = os.path.join(
                self.gm.userpts_dir,f"./{self.gm.spatial_name}_userpts_{self.gm.satellite_name}_{date}.csv"
            )

            export_df = pts2df(
                burned_pts = points_burned_stream.data,
                unburned_pts = points_unburned_stream.data
            )

            if self.gm.userpts_dir is None:
                self.gm.userpts_dir = "./tmp_userpts"


            if export_df.empty:
                pn.state.notifications.error('No Points Have Been Selected For This Granule.',duration=3000)

                return
            
            os.makedirs(self.gm.userpts_dir,exist_ok=True)

            date = analysis_date_selector.value

            points_csv_file = os.path.join(
                self.gm.userpts_dir,f"./{self.gm.spatial_name}_userpts_{self.gm.satellite_name}_{date}.csv"
            )
            
            proceed = True
            if os.path.exists(points_csv_file):
                proceed = False
                if 'Overwrite Points' in selected_options:
                    proceed = True

            if proceed:
                export_df.to_csv(points_csv_file,index=False)
                pn.state.notifications.success('Points exported to file.',duration=3000)

                n_burned = len(export_df[export_df['isBurned']==1])
                n_unburned = len(export_df[export_df['isBurned']==0])
                self.gm.update_analysis_status(date, f'{n_burned} burned points, {n_unburned} unburned points')

                if getattr(self.gm, 'userpts_by_date', None) is None:
                    self.gm.userpts_by_date = {}
                self.gm.userpts_by_date[date] = points_csv_file
                self.registry.save_json()

            else:
                pn.state.notifications.error('Points Have Already Been Exported For This Granule. Select Overwrite Points To Ignore.',duration=3000)

                return
            
        def reload_table(event):
            table.object = self.table_pane(return_df=False).object
            
        export_button.on_click(export_button_callback)
        export_button.on_click(reload_table)

            
        return pn.Row(
            pn.Column(
                instr,
                pn.pane.Alert(
                    "To improve categorization performance, recommend selecting at least 50-100 burned and unburned points each from a variety of locations across the image.",
                    sizing_mode='stretch_width',alert_type='warning',width=250,),
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
                pn.layout.Divider(),
                table,
                width=600,
                sizing_mode='stretch_height'
            ),
            pn.Column(
                pn.pane.Markdown("## Select Pixels"),
                pn.Row(load_pts_button,export_button,export_options),
                hv_pane,
            margin=(40, 40), styles={'background': '#f0f0f0'},
            sizing_mode='stretch_both'
            ))

    def panel(self):
        return pn.Row(self.view,)