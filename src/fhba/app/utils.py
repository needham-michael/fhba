import importlib
import geopandas as gpd
import holoviews as hv
import pandas as pd
import panel as pn
import shapely

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

def initialize_userpolys(color='red',alpha=0.5):

    polys = hv.Polygons(data=None,label='label').opts(color=color,alpha=alpha,active_tools=['poly_draw'])
    poly_stream = hv.streams.PolyDraw(source=polys)

    return polys, poly_stream

def pts2df(burned_pts,unburned_pts):

    burned_df = pd.DataFrame(burned_pts)
    burned_df['isBurned'] = [1 for x in range(burned_df.shape[0])]
    unburned_df = pd.DataFrame(unburned_pts)
    unburned_df['isBurned'] = [0 for x in range(unburned_df.shape[0])]
    
    export_df = pd.concat([burned_df,unburned_df])

    return export_df

def polys2gdf(poly_stream,crs=None):

    geoms = []

    for xcoords, ycoords in zip(poly_stream.data['xs'],poly_stream.data['ys']):
        geoms.append(shapely.Polygon([[x,y] for x,y in zip(xcoords,ycoords)]))

    return gpd.GeoDataFrame(geometry=geoms,crs=crs)