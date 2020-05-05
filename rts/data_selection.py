# -*- coding: utf-8 -*-
"""
Created on Thu Apr 23 11:35:49 2020

@author: disbr007
"""
import copy
import datetime
import numpy as np
import os

import geopandas as gpd
import pandas as pd

from dem_utils.dem_selector import dem_selector
from dem_utils.dem_utils import dems2aoi_ovlp, dems2dems_ovlp, get_matchtag_path, combined_density
from misc_utils.logging_utils import create_logger
from misc_utils.gpd_utils import remove_unused_geometries

logger = create_logger(__name__, 'sh', 'DEBUG')


def dayofyear_diff(d1, d2):
    doy1 = pd.to_datetime(d1).dt.dayofyear
    doy2 = pd.to_datetime(d2).dt.dayofyear
    doy_diff = abs(doy1 - doy2)
    
    return doy_diff


def rank_dem_pair(density, ovlp_perc, date_diff, doy_diff):
    density_score = np.interp(density, (0, 1), (0, 10))
    ovlp_perc_score = np.interp(ovlp_perc, (0, 1), (0, 10))
    date_diff_score = np.interp(date_diff, (0, 700), (0, 10))
    doy_score = np.interp(-doy_diff, (-100, 0), (0, 10))
    
    score = ((density_score * 2) + ovlp_perc_score + date_diff_score + doy_score)*2
    
    return round(score, 2)


#%% Set up
# Inputs
out_dir = r'E:\disbr007\umn\ms\selection\footprints\aoi1'
out_name = 'aoi1_dem_fps.gpkg'
out_layer = 'ao1_dem_selection'
out_catalogids = 'selected_catalogids.txt'
aoi_p = r'E:\disbr007\umn\ms\selection\aoi\aoi1.shp'
dem_fp = r'E:\disbr007\umn\ms\selection\footprints\aoi1_dem_footprints_lps.shp'
# dem_fp = r'E:\disbr007\arctic_dem\ArcticDEM_Strip_Index_Rel7\ArcticDEM_Strip_Index_Rel7.shp'
months = [5, 6, 7, 8, 9, 10]
multispec = True
density_thresh = 0.25
# Existing field names
acqdate = 'acquisitio' #'acqdate1'
filepath = 'local_file' #'fileurl'
orig_id_col = 'pairname'
catalogid1 = 'catalogid1'
n_select = 1

# Params
id_col = 'pair'
lsuffix = 'd1'
rsuffix = 'd2'
orig_id_left = '{}_{}'.format(orig_id_col, lsuffix)
orig_id_right = '{}_{}'.format(orig_id_col, rsuffix)
mtp1 ='matchtag_path_d1'
mtp2 = 'matchtag_path_d2'
combo_dens = 'combo_dens'
date_diff = 'date_diff'
doy_diff = 'DOY_diff'
acqdate1 = '{}_{}'.format(acqdate, lsuffix)
acqdate2 = '{}_{}'.format(acqdate, rsuffix)
filepath1 = '{}_{}'.format(filepath, lsuffix)
filepath2 = '{}_{}'.format(filepath, rsuffix)
inters_geom = 'inters_geom'
ovlp_perc = 'aoi_ovlp_perc'
rank = 'rank'

#%% Select AOI DEMs
logger.info('Identifying DEMs that meet intial criteria...')
dems = dem_selector(aoi_p,
                    DEM_FP=dem_fp,
                    MONTHS=months,
                    DATE_COL=acqdate,
                    MULTISPEC=multispec,
                    LOCATE_DEMS=False)
                    # DENSITY_THRESH=density_thresh)

aoi = gpd.read_file(aoi_p)
if aoi.crs != dems.crs:
    dems = dems.to_crs(aoi.crs)

#%% Rank DEMs - Density
logger.info('Ranking DEMs...')
# Get overlap percentages with each other and with their intersections and an AOI
logger.debug('Identifying overlaping DEMs and computing area and percent overlap...')
dem_ovlp = dems2dems_ovlp(dems)
logger.debug('Computing area and percent overlap with AOI...')
dem_ovlp = dems2aoi_ovlp(dem_ovlp, aoi)

# Matchtag density
logger.debug('Computing matchtag density...')
dem_ovlp[mtp1] = dem_ovlp.apply(lambda x: get_matchtag_path(x[filepath1]), axis=1)
dem_ovlp[mtp2] = dem_ovlp.apply(lambda x: get_matchtag_path(x[filepath2]), axis=1)
dem_ovlp[combo_dens] = dem_ovlp.apply(lambda x: combined_density(x[mtp1], x[mtp2], 
                                                                   x['inters_geom'],
                                                                   clip=True,
                                                                   in_mem_epsg=dem_ovlp.crs.to_epsg()), axis=1)
#%% Rank DEMs - Dates
logger.debug('Calculating total number of days between DEM pairs...')
# Number of days between
dem_ovlp[date_diff] = abs(pd.to_datetime(dem_ovlp[acqdate1]) - pd.to_datetime(dem_ovlp[acqdate2])) / datetime.timedelta(days=1)
# Number of days between regardless of year
logger.debug('Calculating number of days without year between DEM pairs...')
dem_ovlp[doy_diff] = dayofyear_diff(dem_ovlp[acqdate1], dem_ovlp[acqdate2])

dem_rankings = copy.deepcopy(dem_ovlp)

dem_rankings[rank] = dem_rankings.apply(lambda x: rank_dem_pair(x[combo_dens], 
                                                                x[ovlp_perc],
                                                                x[date_diff],
                                                                x[doy_diff]), axis=1)

#%% Select DEMs
dem_rankings.sort_values(by=rank, ascending=False, inplace=True)
selection = dem_rankings.iloc[0:n_select]

#%% Write out selection
# Write filepaths out
# TODO: Add support for getting both images in crosstrack pair
# Get both catalogid1 field names
catalogid_fields = [cf for cf in list(selection) if catalogid1 in cf]
# Get all catalogids in those field names
selected_catids = [cid for fld in catalogid_fields for cid in list(selection[fld])]
# Write catalogids to file
catalogids_out = os.path.join(out_dir, out_catalogids)
with open(catalogids_out, 'w') as outtxt:
    for catid in selected_catids:
        outtxt.write('{}\n'.format(catid))

# Write intersection as files
# intersection_out = r'E:\disbr007\umn\ms\selection\footprints\ovlp\aoi1_test_int.shp'
# logger.info('Writing intersection footprint to file: {}'.format(intersection_out))
# remove_unused_geometries(selection).to_file(intersection_out)

# Write both footprint as file
# pns = [selection[orig_id_left].iloc[0], selection[orig_id_right].iloc[0]]
pns = np.array([list(selection[orig_id_left]), list(selection[orig_id_right])]).flatten()
orig_fps = dems[dems[orig_id_col].isin(pns)]

selected_footprints_out = os.path.join(out_dir, out_name)
logger.info('Writing original footprints to file: {}'.format(selected_footprints_out))
remove_unused_geometries(orig_fps).to_file(selected_footprints_out, layer=out_layer, driver='GPKG')