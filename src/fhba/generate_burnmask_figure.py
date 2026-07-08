
import matplotlib.pyplot as plt
import rioxarray as rxr
import pandas as pd

from blume.table import table

def generate_burnmask_figure(date,initial_date,filename,annotation,county_gdf,proj):

    # Read relevant files
    ds = rxr.open_rasterio(filename).squeeze()
    df = pd.read_csv(filename.replace(".tif",".csv")).sort_values(by=['state_name','county_name'])

    # Format the table for display
    df['County'] = [c.split()[0] + f" ({s})" if s=='OK' else c.split()[0] for c,s in zip(df['county_name'],df['state_name']) ]
    df['County'] = [c + " " for c in df['County']]
    df['Acres Burned'] = [f"{x:,.0f} " for x in df['burned_area_acres']]
    
    cols = ['County','Acres Burned']
    cellText = [list(x) for x in list(df[cols].to_numpy())]

    if date != initial_date:
        fig_title = f"Flint Hills Acreage Burned ({pd.to_datetime(initial_date).strftime("%B %d")} - {pd.to_datetime(date).strftime("%B %d, %Y")})"

    else:
        fig_title = f"Flint Hills Acreage Burned ({pd.to_datetime(date).strftime("%B %d, %Y")})"

    # -------------------------------------------------------------------------
    # Construct the figure

    fig, [ax,ax_table] = plt.subplots(
        ncols=2,subplot_kw=dict(projection=proj,frameon=False),
        dpi=600,layout='compressed')

    fig.get_layout_engine().set(w_pad=4/72)
    # fig, ax = plt.subplots(ncols=1,subplot_kw=dict(projection=proj,frameon=False),dpi=200,layout='tight')

    ax.add_geometries(county_gdf['geometry'],facecolor='beige',edgecolor='k',crs=county_gdf.crs,lw=0.25)
    
    ax.pcolormesh(ds.x,ds.y,ds.where(ds==1),transform=proj,zorder=10,cmap='Reds_r')
    
    ax.patch.set_visible(False)
    
    table(ax_table,cellText=cellText,colLabels=cols,cellLoc='right',bbox=(0,0,1,1))
    
    fig.suptitle(fig_title,ha='center',fontsize='medium')
    
    for geom, label in zip(county_gdf['geometry'],county_gdf['NAME']):
        x,y = geom.centroid.xy
    
        ax.text(s=label.split()[0],x=x[0],y=y[0],ha='center',fontsize=4,fontweight='bold')
    
    ax_table.annotate(annotation.upper(),xy=(0,-0.025),xycoords='axes fraction',fontsize='xx-small',ha='left',fontfamily='monospace')

    png_filename = filename.replace(".tif",".png")
    plt.savefig(png_filename,bbox_inches='tight')