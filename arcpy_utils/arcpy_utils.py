# -*- coding: utf-8 -*-
"""
Created on Wed Nov 20 10:45:25 2019

@author: disbr007
"""
import os

import arcpy

#from misc_utils.id_parse_utils import pgc_index_path
from misc_utils.logging_utils import create_logger

logger = create_logger(__name__, 'sh', 'DEBUG')

arcpy.env.overwriteOutput = True


def pgc_index_path(ids=False):
    '''
    Returns the path to the most recent pgc index from a manually updated
    text file containing the path.
    '''
    with open(r'C:\code\pgc-code-all\config\pgc_index_path.txt', 'r') as src:
        content = src.readlines()
    if not ids:
        index_path = content[0].strip('\n')
    if ids:
        index_path = content[1].strip('\n')
    logger.debug('PGC index path loaded: {}'.format(index_path))

    return index_path


def load_pgc_index(mfp_path=None, where=None):
    """
    Loads the PGC master footprint with an optional where clause and returns 
    it as an arcpy layer object.
    
    Parameters:
    mfp_path (str): path to master footprint
    where    (str): SQL query to pass when loading master footprint
    """
    if not mfp_path:
        mfp_path = pgc_index_path()
    # Location in memory to save result 
    mem_lyr = r'memory/pgc_index_temp'
    if where:
        idx_lyr = arcpy.MakeFeatureLayer_management(mfp_path, mem_lyr,
                                                    where_clause=where)
    else:
        idx_lyr = arcpy.MakeFeatureLayer_management(mfp_path, mem_lyr)
    
    ## Check number returned features is not 0
    result = arcpy.GetCount_management(idx_lyr)
    count = int(result.getOutput(0))
    logger.debug('Loaded features from master footprint {}'.format(count))
    print('Loaded features from master footprint {}'.format(count))
    if count == 0:
        logger.warning('0 features returned from selection.')
    
    return mem_lyr


def type_parser(filepath):
    '''
    takes a file path (or dataframe) in and determines whether it is a dbf, 
    excel, txt, csv (or df), ADD SUPPORT FOR SHP****
    '''
    if type(filepath) == str:
        ext = os.path.splitext(filepath)[1]
        if ext == '.csv':
            with open(filepath, 'r') as f:
                content = f.readlines()
                for row in content[0]:
                    if len(row) == 1:
                        return 'id_only_txt' # txt or csv with just ids
                    elif len(row) > 1:
                        return 'csv' # csv with columns
                    else:
                        print('Error reading number of rows in csv.')
        elif ext == '.txt':
            return 'id_only_txt' 
        elif ext in ('.xls', '.xlsx'):
            return 'excel'
        elif ext == '.dbf':
            return 'dbf'
        elif ext == '.shp':
            return 'shp'
        elif ext == '.pkl':
            return 'pkl'
#    elif isinstance(filepath, gpd.GeoDataFrame):
#        return 'df'
    else:
        print('Unrecognized file type.')
        
        
def read_ids(ids_file, field=None, sep=None, stereo=False):
    '''Reads ids from a variety of file types. Can also read in stereo ids from applicable formats
    Supported types:
        .txt: one per line, optionally with other fields after "sep"
        .dbf: shapefile's associated dbf    
    field: field name, irrelevant for text files, but will search for this name if ids_file is .dbf or .shp
    '''
    ids = []
    # Determine file type
    file_type = type_parser(ids_file)
    # Text file
    if file_type == 'id_only_txt':
        with open(ids_file, 'r') as f:
            content = f.readlines()
            for line in content:
                if sep:
                    # Assumes id is first
                    the_id = line.split(sep)[0]
                    the_id = the_id.strip()
                else:
                    the_id = line.strip()
                ids.append(the_id)
    # DBF
    elif file_type == 'dbf':
        df = gpd.read_file(ids_file)
        if field == None:
            id_col = determine_id_col(df)
        else:
            id_col = field
        df_ids = list(df[id_col])
        for each_id in df_ids:
            ids.append(each_id)
        # If stereopairs are desired, find them
        if stereo == True:
            sp_ids = get_stereopair_ids(df)
            for sp_id in sp_ids:
                ids.append(sp_id)
    # SHP
    elif file_type == 'shp':
        df = gpd.read_file(ids_file)
        if field:
            ids = list(df[field].unique())
        else:
            id_fields = ['catalogid', 'catalog_id', 'CATALOGID', 'CATALOG_ID']
            field = [x for x in id_fields if x in list(df)]
            if len(field) != 1:
                logger.error('Unable to read IDs, no known ID fields found.')
            else:
                field = field[0]
            ids = df[field].unique()

    # PKL
    elif file_type == 'pkl':
        logger.warning('Loading IDs from pkl, not sure if this works...')
        df = pd.read_pickle(ids_file)
        if len(df.columns) > 1:
            ids = list(df[df.columns[0]])
        elif len(df.columns) == 1:
            ids = list(df)
        else:
            print('No columns found in pickled dataframe.')
    
    # Excel
    # This assumes single column of IDs with no header row
    elif file_type == 'excel':
        df = pd.read_excel(ids_file, header=None, squeeze=True)
        ids = list(df)
        
    else:
        print('Unsupported file type... {}'.format(file_type))

    return ids


def get_unique_ids(table, field, where=None, clean_fxn=None):
    """
    Loads unique IDs from the given field in the given table, optionally
    with the provided where clause, optionally applying a function to
    each id before returning.

    Parameters:
    table: os.path.abspath
        The path to the table to parse.
    field: str
        The field in table to parse.
    where: str
        SQL WHERE clause to subset table.
    clean_fxn: function
        Function to apply to each ID before returning.

    Returns:
    set: unique values from the given field
    """
    if type(table) == str:
        logger.debug('Loading {} {} WHERE {}'.format(os.path.basename(table), field, where))

    unique_ids = set()
    logger.info('Field: {}'.format(field))
    for row in arcpy.da.SearchCursor(in_table=table, field_names=[field], where_clause=where):
        the_id = row[0]
        if clean_fxn:
            the_id = clean_fxn(the_id)
        unique_ids.add(the_id)

    logger.debug('Unique IDs: {:,}'.format(len(unique_ids)))

    return unique_ids


def get_count(layer):
    result = arcpy.GetCount_management(layer)
    count = int(result.getOutput(0))

    return count
