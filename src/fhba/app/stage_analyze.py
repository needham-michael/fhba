import os

from functools import lru_cache

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

        print("**** table_pane called with:")
        print("self.gm.to_df")
        print(self.gm.to_df())

        gm_df = self.gm.to_df().reset_index()
        gm_df = gm_df.rename(columns={'index':'date'})
        gm_df = gm_df[gm_df['download_status'] == True]
        gm_df = gm_df.sort_values(by='date')

        table = pn.pane.DataFrame(
            gm_df[['date','user_categorization','analysis_status']], index=False, sizing_mode='stretch_both'
        )

        if return_df:
            return table, gm_df
        
        return table, gm_df
    
    def get_widgets(self, gm_df):

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

        # show_fire_checkbox = pn.widgets.Checkbox(
        #     name="Show HMS Active Fire Overlay", value=True
        # )
        
        fire_opacity_slider = pn.widgets.FloatSlider(
            name="Active Fire Opacity", start=0.0, end=1.0, step=0.05,
            value=1.0, width=180,
        )

        overlay_loading = pn.indicators.LoadingSpinner(
            name="Loading HMS Overlay...", width=20, height=20, visible=False, value=False) 
        
        loading = pn.indicators.LoadingSpinner(
            name="Loading Image...", width=200, height=25,visible=False,value=False)
        
        export_button = pn.widgets.Button(
            name='Export Points', button_type='danger')

        export_options = pn.widgets.CheckBoxGroup(
            name='Export Options', 
            value=[], 
            options=['Overwrite Points']
        )

        return (analysis_date_selector, load_img_button, load_pts_button, 
                fire_opacity_slider, overlay_loading, loading, 
                export_button, export_options)

    @param.depends('year','satellite','registry', 'gm')
    def view(self):
        instr = get_instructions("05_instr_select_pixels.md",instr_width=250)
        instr.objects += [pn.pane.Alert(
                    "To improve categorization performance, recommend selecting at least 50-100 burned and unburned points each from a variety of locations across the image.",
                    sizing_mode='stretch_width',alert_type='warning')
        ]

        table, gm_df = self.table_pane(return_df=True)

        # -------------------------------- SETUP WIDGETS -------------------------------

        analysis_date_selector, load_img_button, load_pts_button, \
            fire_opacity_slider, overlay_loading, loading, export_button, export_options = self.get_widgets(gm_df)

        # INITIALIZE ELEMENTS FOR HV PANE
        hv_pane = pn.pane.HoloViews(None, width=500, height=1000)

        points_burned, points_burned_stream = initialize_userpoints(color='red',label='Burned')
        points_unburned, points_unburned_stream = initialize_userpoints(color='blue',marker='+',label='Unburned')
        fire_overlay = initialize_userpoints(
            color='orange',marker='o',label='HMS Active Fire',point_locations=([],[]))
        
        print(f"{fire_overlay = }")  # Debugging statement

        self.current_date = None
        self.current_rgb = None
        self.current_fire_overlay = fire_overlay
        self.current_points_burned = points_burned
        self.current_points_unburned = points_unburned

        # ----------------------------- Callback Functions -----------------------------

        def reset_user_points(event):
            points_burned.data = pd.DataFrame({'x':[],'y':[]})
            points_unburned.data = pd.DataFrame({'x':[],'y':[]})
            self.current_points_burned = points_burned
            self.current_points_unburned = points_unburned

        def construct_hvpane(event):
            if self.current_rgb is not None:
                hv_pane.object = \
                    self.current_rgb * self.current_points_burned * \
                    self.current_points_unburned * self.current_fire_overlay

        def update_hv_rgb(event):
            
            date = analysis_date_selector.value
            print(f"Updating HV RGB with {date = }")  # Debugging statement
            
            loading.value = True
            loading.visible = True
            loading.name='Loading Image...'

            # Skip reloading the image if the same date is selected again
            if date == self.current_date and self.current_rgb is not None:
                pn.state.notifications.info(
                    f"Image for date {date} is already loaded.", duration=3000)
                loading.value = False
                loading.visible = False
                return

            pn.state.notifications.info(
                f"Loading image for analysis date: {analysis_date_selector.value}",
                duration=3000)
            
            try:
                rgb = self.gm.get_nir_red_hv_rgb(
                    date=date,in_app=True,include_counties=True)
                
            except Exception as e:
                msg = f"Error loading image for date {date}: {str(e)}"
                print(msg)
                pn.state.notifications.error(msg,duration=6000)
                loading.value = False
                loading.name='Error Loading Image.'
                return
            
            if isinstance(rgb, str):
                pn.state.notifications.error(rgb, duration=6000)
                loading.value = False
                loading.visible = False
                loading.name = 'No data available.'
                return
            
            self.current_rgb = rgb
            self.current_date = date     
            loading.value = False
            loading.visible = False

        def update_fire_overlay(event):
            
            date = analysis_date_selector.value

            overlay_loading.visible = True
            overlay_loading.value = True
                
            self.current_fire_overlay = self.gm.get_hms_active_fire_overlay(date)

            if self.current_rgb is not None:
                construct_hvpane(event)

            overlay_loading.visible = False
            overlay_loading.value = False   

        def update_fire_overlay_opacity(event):

            overlay_loading.visible = True
            overlay_loading.value = True

            if self.current_fire_overlay is not None:
                for element in self.current_fire_overlay.items():
                    element[1].opts(alpha=fire_opacity_slider.value)
                # self.current_fire_overlay.opts(alpha=fire_opacity_slider.value)

            if self.current_rgb is not None:
                construct_hvpane(event)

            overlay_loading.visible = False
            overlay_loading.value = False

        def load_user_points(event):

            date = analysis_date_selector.value

            print(f"Updating HV RGB with {date = }")  # Debugging statement

            points_csv_file = os.path.join(
                self.gm.userpts_dir,
                f"./{self.gm.spatial_name}_userpts_{self.gm.satellite_name}_{date}.csv"
            )

            if not os.path.exists(points_csv_file):
                pn.state.notifications.error(
                    f"No previous points found for date {date}.",duration=6000)
                return

            print(f"Loading user points from {points_csv_file}")  # Debugging statement

            df_userpts = pd.read_csv(points_csv_file)

            df_isburned = df_userpts[df_userpts['isBurned']==1]
            df_unburned = df_userpts[df_userpts['isBurned']==0]

            points_burned.data = pd.DataFrame(
                {'x':df_isburned['x'].tolist(),'y':df_isburned['y'].tolist()})
            points_unburned.data = pd.DataFrame(
                {'x':df_unburned['x'].tolist(),'y':df_unburned['y'].tolist()})
            
            self.current_points_burned = points_burned
            self.current_points_unburned = points_unburned

            loading.value = True
            loading.visible = True
            loading.name='Loading Image...'

            # try:
            #     rgb = self.gm.get_nir_red_hv_rgb(
            #         date=date,in_app=True,include_counties=True)
            # except Exception as e:
            #     pn.state.notifications.error(
            #         f"Error loading image for date {date}: {str(e)}",duration=6000)
            #     loading.value = False
            #     loading.name='Error Loading Image.'
            
            # if isinstance(rgb, str):
            #     pn.state.notifications.error(rgb)
            #     return

            if self.current_rgb is None:
                update_hv_rgb(event)
            
            update_fire_overlay(event)
            construct_hvpane(event)

            
            # if rgb is not None:
            #     self.current_base = rgb * points_burned * points_unburned
            #     if show_fire_checkbox.value:
            #         try:
            #             fire_overlay = self.gm.get_hms_active_fire_overlay(date, alpha=fire_opacity_slider.value)
            #         except Exception as exc:
            #             print("Could not load active fire overlay: %s", exc)
            #     self.current_fire_overlay = fire_overlay
            #     hv_pane.object = self.current_base * fire_overlay if fire_overlay else self.current_base

            loading.value = False
            loading.visible = False

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

            table.object = self.table_pane(return_df=False)[0].object

        
        load_img_button.on_click(update_hv_rgb)
        load_img_button.on_click(update_fire_overlay)
        load_img_button.on_click(construct_hvpane)
        load_img_button.on_click(reset_user_points)
        
        load_pts_button.on_click(load_user_points)
        load_pts_button.on_click(update_fire_overlay)
        load_pts_button.on_click(construct_hvpane)
            
        export_button.on_click(export_button_callback)
        export_button.on_click(reload_table)

        fire_opacity_slider.param.watch(update_fire_overlay_opacity, 'value')
        # show_fire_checkbox.param.watch(update_fire_overlay, 'value')

        return pn.Row(
            pn.Column(
                instr,
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
                pn.Row(fire_opacity_slider, overlay_loading),
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