# -*- coding: utf-8 -*-
"""
Created on Thu May 14 12:19:21 2020

@author: disbr007
"""
import copy
import operator
import matplotlib.pyplot as plt
import numpy as np

import pandas as pd
import geopandas as gpd

from misc_utils.logging_utils import create_logger
from misc_utils.gpd_utils import select_in_aoi
from obia_utils.ImageObjects import ImageObjects


pd.options.mode.chained_assignment = None

logger = create_logger(__name__, 'sh', 'INFO')

plt.style.use('classic')


#%%
super_obj_p = r'E:\disbr007\umn\2020sep27_eureka\seg\grm_ms' \
              r'\WV02_20140703013631_1030010032B54F00_14JUL03013631-' \
              r'M1BS-500287602150_01_P009_u16mr3413_pansh_test_aoi_468_' \
              r'bst250x0ni0s0spec0x25spat25x0_cln_zs.shp'

obj_p = r'E:\disbr007\umn\2020sep27_eureka\seg\hw_seg' \
        r'\WV02_20140703013631_1030010032B54F00_14JUL03013631-' \
        r'M1BS-500287602150_01_P009_u16mr3413_pansh_test_aoi_' \
        r'bst100x0ni100s0spec0x3spat50x0_cln_zs.shp'

hw_candidates_p = r'E:\disbr007\umn\2020sep27_eureka\scratch' \
                  r'\hwc_adjmedneg0x2_ndvi0_med0_all_bestadj.shp'

rts_candidates_p = r'E:\disbr007\umn\2020sep27_eureka\scratch' \
                  r'\rts_candidates.shp'

aoi_p = r'E:\disbr007\umn\2020sep27_eureka\aois\test_aoi_sub.shp'
aoi_p = None

# Existing column name
med_mean = 'MED_mean'
cur_mean = 'CurPr_mean'
ndvi_mean = 'NDVI_mean'
slope_mean = 'Slope_mean'
rug_mean = 'RugIn_mean'
sa_rat_mean = 'SAratio_me'
elev_mean = 'elev_mean'
# mdfm_mean = 'MDFM_mean'
# edged_mean = 'EdgDen_mea'
# cclass_maj = 'CClass_maj'

value_fields = [
    (med_mean, 'mean'),
    (cur_mean, 'mean'),
    (ndvi_mean, 'mean'),
    (slope_mean, 'mean'),
    (rug_mean, 'mean'),
    (sa_rat_mean, 'mean'),
    (elev_mean, 'mean')
    # (mdfm_mean, 'mean'),
    # (edged_mean, 'mean'),
    # (cclass_maj, 'majority')
    ]

# Created columns
hw_candidate = 'headwall_candidate'
contains_hw = 'contains_hw'
rts_candidate = 'rts_candidate'
truth = 'truth'

# Columns to be converted to strings before writing
to_str_cols = []
#%%
if aoi_p:
    aoi = gpd.read_file(aoi_p)
    logger.info('Subsetting objects to AOI...')
    gdf = select_in_aoi(gpd.read_file(obj_p), aoi, centroid=True)
    ios = ImageObjects(objects_path=gdf, value_fields=value_fields)
else:
    ios = ImageObjects(objects_path=obj_p, value_fields=value_fields)

#%% Merging parameters
# Merge column names
# merge_candidates = 'merge_candidates'
# merge_path = 'merge_path'
# mergeable = 'mergeable'

#%%
# Criteria to determine candidates to be merged. This does not limit
# which objects they may be merge to, that is done with pairwise criteria.
# merge_criteria = [
#                   (ios.area_fld, operator.lt, 1000000),
#                   (ndvi_mean, operator.lt, 0),
#                   # (med_mean, operator.lt, 0.3),
#                   # (slope_mean, operator.gt, 2)
#                  ]
# # Criteria to check between a merge candidate and merge option
# pairwise_criteria = {
#     # 'within': {'field': cur_mean, 'range': 10},
#     'threshold': {'field': ndvi_mean, 'op': operator.lt, 'threshold': 0}
# }

#%% RULESET
# HEADWALLS
# Subset by simple thresholds first
#%% High ruggedness
high_rugged = 0.25
rug_thresh = 'rugged_gt{}'.format(high_rugged)
ios.objects[rug_thresh] = ios.objects[rug_mean] > high_rugged

#%% High surface area ratio
high_sa_rat = 1.01
sa_thresh = 'surf_area_ratio_thresh'
ios.objects[sa_thresh] = ios.objects[sa_rat_mean] > high_sa_rat

#%% High slope
high_slope = 8
slope_thresh = 'slope_thresh'
ios.objects[slope_thresh] = ios.objects[slope_mean] > high_slope

#%% Low NDVI
low_ndvi = 0
ndvi_thresh = 'ndvi_thresh'
ios.objects[ndvi_thresh] = ios.objects[ndvi_mean] < low_ndvi

#%% Low MED
low_med = 0
med_thresh = 'med_thresh'
ios.objects[med_thresh] = ios.objects[med_mean] < low_med

#%% Get neighbors for those objects that meet thresholds
thresholds = [rug_thresh,
              sa_thresh,
              slope_thresh,
              ndvi_thresh,
              med_thresh]

ios.get_neighbors(subset=ios.objects[ios.objects.apply(
    lambda x: np.all([x[c] for c in thresholds]),
    axis=1)])
#%%
ios.compute_area()
# ios.calc_object_stats()
ios.compute_neighbor_values(cur_mean)
ios.compute_neighbor_values(med_mean)

#%% Adjacent to both high and low curvature
high_curv = 40
low_curv = -30
curv_adj_hl = 'adj{}_gt{}_lt{}'.format(cur_mean, high_curv, low_curv)
ios.objects[curv_adj_hl] = (ios.adjacent_to(in_field=cur_mean, op=operator.lt,
                                            thresh=low_curv) &
                            (ios.adjacent_to(in_field=cur_mean, op=operator.gt,
                                             thresh=high_curv)))
best_low_curv = 'b_low_curv'
best_high_curv = 'b_high_curv'
ios.objects[best_low_curv] = ios.best_adjacent_to(in_field=cur_mean,
                                                 op=operator.lt)
ios.objects[best_high_curv] = ios.best_adjacent_to(in_field=cur_mean,
                                                  op=operator.gt)
to_str_cols.extend([best_low_curv, best_high_curv])

#%% Adjacent to low MED
adj_low_med = -0.2
med_adj_l = 'adj{}_lt{}'.format(med_mean, adj_low_med)
ios.objects[med_adj_l] = ios.adjacent_to(med_mean, op=operator.lt,
                                         thresh=adj_low_med)
best_low_med = 'b_low_med'
ios.objects[best_low_med] = ios.best_adjacent_to(in_field=med_mean,
                                                 op=operator.lt)
to_str_cols.append(best_low_med)

#%% All headwall criteria
hw_criteria = [curv_adj_hl,
               med_adj_l,
               rug_thresh,
               sa_thresh,
               slope_thresh,
               ndvi_thresh,
               med_thresh]
ios.objects[hw_candidate] = ios.objects.apply(
    lambda x: np.all([x[c] for c in hw_criteria]), axis=1)

#%%
logger.info('Writing...')
ios.write_objects(hw_candidates_p,
                  to_str_cols=to_str_cols,
                  overwrite=True)
#%% Load written candidates with 'truth'
# hwc_shp = gpd.read_file(r'E:\disbr007\umn\2020sep27_eureka\scratch\hwc_truth.shp')
# # Drop everything but truth and index
# hwc_shp = hwc_shp[['index', 'truth']]
# hwc_shp.set_index('index', inplace=True)
#
# hwc = ios.objects[ios.objects[hw_candidate]==True]
# hwc = hwc.join(hwc_shp)

# %% Plot headwall candidate characteristics
# alpha = 0.5
# linewidth = 2
# vline_color = 'red'
# atts = {rug_mean: high_rugged,
#         sa_rat_mean: high_sa_rat,
#         slope_mean: high_slope,
#         ndvi_mean: low_ndvi,
#         med_mean: low_med,
#         }
# adj_atts = {best_low_curv: low_curv,
#             best_high_curv: high_curv,
#             best_low_med: low_med}
#
# fig, axes = plt.subplots(3, 3, figsize=(15, 10))
# axes = axes.flatten()
#
# truth_filter = hwc[truth] == 'true_yes'
# for i, (k, v) in enumerate(atts.items()):
#     axes[i].set_title(k)
#     # hwc[[k]].hist(k, alpha=alpha, label='F', ax=axes[i])
#     axes[i].hist([hwc[k][truth_filter], hwc[k][~truth_filter]],
#                  stacked=True,
#                  label=['true_yes', 'true_no'] if i == 0 else "",
#                  alpha=alpha)
#     axes[i].axvline(v, linewidth=linewidth, color=vline_color)
#
# for j, (k, v) in enumerate(adj_atts.items()):
#     axes[i+j+1].hist([hwc[~hwc[k].isnull()][best_low_curv].apply(lambda x: x[1])[truth_filter],
#                       hwc[~hwc[k].isnull()][best_low_curv].apply(lambda x: x[1])[~truth_filter]],
#                      alpha=alpha,
#                      stacked=True),
#     axes[i+j+1].set_title(k)
#     axes[i+j+1].axvline(v, linewidth=linewidth, color=vline_color)
#
# l = fig.legend(loc="upper left")
# plt.tight_layout()
# fig.show()


#%% Find RTS
# Load super objects
so = ImageObjects(super_obj_p,
                  value_fields=value_fields)
#%%
# Find objects that contain potential headwalls
so.objects[contains_hw] = so.objects.geometry.apply(
    lambda x: np.any([x.contains(hw_p) for hw_p in
                      ios.objects[ios.objects[hw_candidate]].centroid.values]))

# Find objects with low ndvi
so.objects[ndvi_thresh] = so.objects[ndvi_mean] < low_ndvi
# Find objects with low MED
so.objects[med_thresh] = so.objects[med_mean] < low_med
# Find objects with slope greater than threshold
so.objects[slope_thresh] = so.objects[slope_mean] > 3
# Find objects
#%% Determine if all criteria met
rts_criteria = [contains_hw,
                ndvi_thresh,
                med_thresh,
                slope_thresh]

so.objects['rts_candidate'] = so.objects.apply(
    lambda x: np.all([x[c] for c in rts_criteria]), axis=1)

#%% Write RTS candidates
so.write_objects(rts_candidates_p,
                 to_str_cols=to_str_cols,
                 overwrite=True)
#%%
# Find neighbors for objects that contain headwall candidate
# so.get_neighbors(so.objects[so.objects[contains_hw is True]])


#%%
# so.compute_neighbor_values()
#%%
# Determines merge paths
# ios.pseudo_merging(merge_fields=[med_mean, ndvi_mean],
#                    merge_criteria=merge_criteria,
#                    pairwise_criteria=pairwise_criteria)
#%%
# Does merging
# ios.merge()
#%% object with value within distance
# obj_p = r'E:\disbr007\umn\2020sep27_eureka\scratch\region_grow_objs.shp'
# obj = gpd.read_file(obj_p)
# field = 'CurPr_mean'
# candidate_value = 25
# dist_to_value = -25
# dist = 2
#
# selected = obj[obj[field] > candidate_value]
#
# for i, r in selected.iterrows():
#     if i == 348:
#         tgdf = gpd.GeoDataFrame([r], crs=obj.crs)
#         within_area = gpd.GeoDataFrame(geometry=tgdf.buffer(dist), crs=obj.crs)
#         # overlay
#         # look up values for features in overlay matches
#         # if meet dist to value, True

