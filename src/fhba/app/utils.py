import importlib
import holoviews as hv
import pandas as pd
import panel as pn

def get_instructions(filename,instr_width):

    instructions = importlib.resources.open_text(
        'fhba.app.appdata.instructions', filename
    ).readlines()

    return pn.pane.Markdown("".join(instructions),width=instr_width)

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

def pts2df(burned_pts,unburned_pts):

    burned_df = pd.DataFrame(burned_pts)
    burned_df['isBurned'] = [1 for x in range(burned_df.shape[0])]
    unburned_df = pd.DataFrame(unburned_pts)
    unburned_df['isBurned'] = [0 for x in range(unburned_df.shape[0])]
    
    export_df = pd.concat([burned_df,unburned_df])

    return export_df