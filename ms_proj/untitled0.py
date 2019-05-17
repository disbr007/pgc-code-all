# -*- coding: utf-8 -*-
"""
Created on Wed May  1 13:16:13 2019

@author: disbr007
"""

import geopandas as gpd
import fiona
import pandas as pd
from tqdm import tqdm
import sys, os

from query_danco import query_footprint
sys.path.insert(0, r'C:\code\misc_utils')
from id_parse_utils import read_ids

layer = 'pgc_imagery_catalogids'

index = query_footprint(layer, table=True)
index_ids = list(index.catalog_id)

path_to_ids = r"E:\disbr007\UserServicesRequests\Projects\1539_CIRES_Herzfeld\3740\catalogids.txt"
src_ids = read_ids(path_to_ids)

print('Finding onhand ids...')
oh = [x for x in tqdm(src_ids) if x in index_ids]

print('Finding not onhand ids...')
noh = [x for x in tqdm(src_ids) if x not in index_ids]