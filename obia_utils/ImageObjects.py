import copy
import operator
from random import randint
import time

import numpy as np
from osgeo import ogr, gdal
import pandas as pd
import geopandas as gpd
from tqdm import tqdm

from misc_utils.logging_utils import create_logger
from misc_utils.gpd_utils import write_gdf
from misc_utils.RasterWrapper import Raster

import matplotlib.pyplot as plt
plt.style.use('pycharm')

# TODO: Make Rule a class

# Suppress pandas SettingWithCopyWarning
pd.options.mode.chained_assignment = None

logger = create_logger(__name__, 'sh', 'DEBUG')

#%%
def weighted_mean(values, weights):
    weight_proportions = [i / sum(weights) for i in weights]
    wm = sum([v * w for v, w in zip(values, weight_proportions)])

    return wm


def weighted_majority(values, weights):
    weighted_values = [(v, w) for v, w in zip(values, weights)]
    wmaj = max(weighted_values, key=operator.itemgetter(1))
    return wmaj


# Pairwise functions
def within_range(a, b, range):
    return operator.le(abs(a - b), range)


def pairwise_match(row, possible_match, pairwise_criteria : list):
    """Tests each set of pairwise critieria against the current row
    and a possible match.
    Parameters
    ---------
    row : pd.Series
        Must contain all fields in pairwise criteria
    possible_match : pd.Series
        Must contain all fields in pairwise critieria
    pairwise_criteria : list
        List of dicts:
        Dict of critiria, supported types:
            'within': {'field': "field_name", 'range': "within range"}
            'threshold: {'field': "field_name, 'op', operator comparison fxn,
                         'threshold': value to use in fxn}
    Returns
    -------
    bool : True is all criteria are met
    """
    # If no pairwise criteria provided, mark as True
    if pairwise_criteria is None:
        return True

    criteria_met = []
    for criteria_type, params in pairwise_criteria.items():
        if criteria_type == 'within':
            met = within_range(row[params['field']],
                               possible_match[params['field']],
                               params['range'])
            # logger.debug('{} {} {} {}: {}'.format(params['field'],
            #                                       criteria_type,
            #                                       params['op'],
            #                                       params['range'],
            #                                       met))
            criteria_met.append(met)
        elif criteria_type == 'threshold':
            if params['threshold'] == 'self':
                threshold = row[params['field']]
            else:
                threshold = params['threshold']
            met = params['op'](possible_match[params['field']],
                               threshold)
            # logger.debug('{} {} {} {}: {}'.format(params['field'],
            #                                       criteria_type,
            #                                       params['op'],
            #                                       params['threshold'],
            #                                       met))
            criteria_met.append(met)

    return all(criteria_met)


def z_score(value, mean, std):
    return (value - mean) / std


def abs_stds(value1, value2, std):
    return abs((value1 - value2) / std)


def rule_field_name(rule):
    fn = '{}_{}{}'.format(rule['in_field'],
                           str(rule['op'])[-3:-1],
                           str(rule['threshold']).replace('.', 'x'))
    if rule['rule_type'] == 'adjacent':
        fn = '{}_{}'.format('adj', fn)
    return fn


def create_rule(rule_type, in_field, op, threshold, out_field=None, **kwargs):
    supported_rule_types = ['threshold', 'adjacent']
    if rule_type not in supported_rule_types:
        logger.error('Unsupported rule_type "{}". Must be in: '
                     '{}'.format(rule_type, supported_rule_types))
    rule = {'rule_type': rule_type,
            'in_field': in_field,
            'op': op,
            'threshold': threshold}

    if out_field is not None:
        if out_field is True:
            rule['out_field'] = rule_field_name(rule)
        else:
            rule['out_field'] = out_field

    return rule


def overlay_any_objects(geometry, others, centroid=True, predicate='contains',
                         threshold=None,
                         other_value_field=None,
                         op=None):
    """Determines if any others are related to geometry, based on spatial
     predicate, optionally using the centroids of others, optionally
     using a threshold on others to reduce the number of others that are
    considered"""
    if threshold:
        # Subset others to only include those that meet threshold provided
        others = others[op(others[other_value_field], threshold)]

    # Determine if object contains others
    if centroid:
        others_geoms = others.geometry.centroid.values
    else:
        others_geoms = others.geometry.values
    if predicate == 'contains':
        overlays = np.any([geometry.contains(og) for og in others_geoms])
    elif predicate == 'within':
        overlays = np.any([geometry.within(og) for og in others_geoms])
    elif predicate == 'intersects':
        overlays = np.any([geometry.intersects(og) for og in others_geoms])
    elif predicate == 'disjoint':
        overlays = np.any([geometry.disjoint(og) for og in others_geoms])
    elif predicate == 'overlaps':
        overlays = np.any([geometry.overlaps(og) for og in others_geoms])
    elif predicate == 'touches':
        overlays = np.any([geometry.touches(og) for og in others_geoms])

    return overlays


class ImageObjects:
    """
    Designed to facilitate object-based-image-analysis
    classification.
    """
    def __init__(self, objects_path, value_fields=None):
        if isinstance(objects_path, gpd.GeoDataFrame):
            self.objects = copy.deepcopy(objects_path)
            self.objects_path = None
        else:
            self.objects_path = objects_path
            self.objects = gpd.read_file(objects_path)

        logger.info('Loaded {:,} objects.'.format(len(self.objects)))

        # Field names
        self.nebs_fld = 'neighbors'
        self._area_fld = 'area'
        self.compact_fld = 'compactness'
        self.class_fld = 'class'

        # Merge column names
        self.mc_fld = 'merge_candidates'
        self.mp_fld = 'merge_path'
        # Inits to True, marked False if merged, or considered and unmergeable
        self.m_fld = 'mergeable'
        self.m_seed_fld = 'merge_seed'
        self.m_ct_fld = 'merge_count'
        self.continue_iter = 'continue_iter'
        self.mergeable_ids = None

        # List of (field_name, summary_stat) to be recalculated after merging
        self.value_fields = self._parse_value_fields(value_fields)

        # List of boolean fields holding result of apply a rule
        self.rule_fields = []
        # Properties calculated on demand
        self._num_objs = None
        self._fields = list(self.objects.columns)
        self._object_stats = None
        self._area = None
        # Neighbor value fields
        self.nv_fields = list()
        self.objects[self.nebs_fld] = np.NaN
        # Rules
        self._rule_fld_name = 'in_field' # field name in rule dictionaries

        # TODO: check for unique index, create if not
        # Name index if unnamed
        if not self.objects.index.name:
            self.objects.index.name = 'index'
        if not self.objects.index.is_unique:
            logger.warning('Non-unique index not supported.')

    def check_neb(self,neb):
        for i, row in self.objects.iterrows():
            if isinstance(row[self.nebs_fld], list):
                if neb in row[self.nebs_fld]:
                    print('check_neb: ', i, row[self.nebs_fld])

    def _parse_value_fields(self, value_fields):
        if isinstance(value_fields, dict):
            # Assume zonal stats dict passed
            # {'name': {'path': path, 'stats': ['mean']}}
            value_fields = {'{}_{}'.format(k, x): x
                           for k, v in value_fields.items()
                           for x in v['stats']}
        elif isinstance(value_fields, list):
            # Assume list of (field_name, summary_stat)
            value_fields = {fn: ss for fn, ss in value_fields}
        # af = self.area_fld
        # value_fields[af] = 'sum'
        return value_fields

    @property
    def fields(self):
        self._fields = list(self.objects.columns)
        return self._fields

    @property
    def area_fld(self):
        self.objects[self._area_fld] = self.objects.area
        return self._area_fld

    @property
    def num_objs(self):
        self._num_objs = len(self.objects)
        return self._num_objs

    @property
    def object_stats(self):
        self._object_stats = self.objects.describe()
        return self._object_stats

    def compute_area(self):
        self.objects[self.area_fld] = self.objects.geometry.area
        self.fields.append(self.area_fld)

    def calc_compactness(self):
        logger.info('Calculating object compactness')
        # Polsby - Popper Score - - 1 = circle
        self.objects[self.compact_fld] = self.objects.geometry.apply(
            lambda x: (np.pi * 4 * x.area) / (x.boundary.length) ** 2)

    def _nv_field_name(self, field):
        return '{}_nv'.format(field)

    def get_value(self, index_value, value_field):
        """Get the value of value_field and index index_value"""
        if value_field not in self.fields:
            logger.error('Field not found: {}'.format(value_field))
            logger.error('Cannot get value for field: {}'.format(value_field))
            raise KeyError

        value = self.objects.at[index_value, value_field]
        return value

    def get_neighbors(self, subset=None):
        """Creates a new column containing IDs of neighbors as list of
        indicies."""
        # If no subset is provided, use the whole dataframe
        if subset is None:
            logger.warning('No subset provided when finding neighbors, '
                           'computation may be slow.')
            subset = copy.deepcopy(self.objects)

        # List to store neighbors
        ns = []
        # List to store unique_ids
        labels = []
        # Iterate over rows, for each row, get indicies of all features
        # it touches
        # logger.debug('Getting neighbors for {} '
        #              'features...'.format(len(subset)))
        pre = time.time()
        for index, row in tqdm(subset.iterrows(),
                               total=len(subset),
                               desc='Finding neighbors'):
            neighbors = self.objects[self.objects.geometry
                                     .touches(row['geometry'])].index.tolist()
            # If the feature is considering itself a neighbor remove it from
            # the list
            # if index in neighbors:
            #     neighbors = neighbors.remove(index)

            # Save the neighbors that have been found and their IDs
            ns.append(neighbors)
            labels.append(index)
        # post = time.time()
        # logger.info('rows: {}'.format(post-pre))
        # pre = time.time()
        # neighbors = [self.objects[self.objects.geometry.touches(t.geometry)].index.values
        #              for t in subset.itertuples()]
        # post = time.time()
        # logger.info('tuples compre: {}'.format(post-pre))

        if not any(ns):
            logger.warning('No neighbors found.')
        # Create data frame of the unique ids and their neighbors
        nebs = pd.DataFrame({self.objects.index.name: labels,
                             self.nebs_fld: ns}).set_index(self.objects.
                                                           index.name,
                                                           drop=True)

        # Combine the neighbors dataframe back into the main dataframe
        self.objects.update(nebs)

        # logger.debug('Neighbor computation complete.')

        return self.objects[self.objects.index.isin(subset.index)]

    def replace_neighbor(self, old_neb, new_neb, update_merges=False):
        """Replace old_neb with new_neb in every objects list of
        neighbors. Optionally, update merge_path field as well."""
        def _rowwise_replace_neighbor(row, old_neb, new_neb, replace_field):
            if isinstance(row[replace_field], list):
                neighbors = row[replace_field]
                if old_neb in neighbors:
                    neighbors = [n for n in neighbors if n != old_neb]
                    # if the new neighbor is not in the list of neighbors already
                    # and the current row is the new neighbor itself, add it
                    if new_neb not in neighbors and row.name != new_neb:
                        neighbors.append(new_neb)
            else:
                neighbors = row[replace_field]

            return neighbors

        # self.objects[self.nebs_fld] = self.objects[self.nebs_fld].apply(
        #     lambda x: _rowwise_replace_neighbor(x, old_neb, new_neb)
        #     if isinstance(x, list) else x)

        self.objects[self.nebs_fld] = self.objects.apply(
            lambda x: _rowwise_replace_neighbor(x,
                                                old_neb,
                                                new_neb,
                                                self.nebs_fld),
            axis=1)

        if update_merges:
            # self.objects[self.mp_fld] = self.objects[self.mp_fld].apply(
            #     lambda x: _rowwise_replace_neighbor(x, old_neb, new_neb)
            #     if isinstance(x, list) else x)
            self.objects[self.mp_fld] = self.objects.apply(
                lambda x: _rowwise_replace_neighbor(x,
                                                    old_neb,
                                                    new_neb,
                                                    self.mp_fld),
                axis=1)

    def replace_neighbor_value(self, neb_v_fld, old_neb, new_neb, new_value):
        """Replace old_nebs value in neb_v_fld with new_neb and new_nebs
        value, new_value."""
        def _rowwise_replace_nv(row, neb_v_fld, old_neb, new_neb, new_value):
            neb_values = row[neb_v_fld]
            if old_neb in neb_values.keys():
                neb_values.pop(old_neb)
                neb_values[new_neb] = new_value
            return neb_values

        # self.objects[neb_v_fld] = self.objects[neb_v_fld].apply(
        #     lambda x: _rowwise_replace_nv(x, old_neb,
        #                                   new_neb, new_value)
        #     if isinstance(x, dict) else x)
        self.objects[neb_v_fld] = self.objects.apply(
            lambda x: _rowwise_replace_nv(x,
                                          neb_v_fld,
                                          old_neb,
                                          new_neb,
                                          new_value)
            if isinstance(x, dict) else x,
            axis=1)

    def neighbor_features(self, subset=None):
        """
        Create a new geodataframe of neighbors (geometries and values)
         for all features in subset. Finds neighbors if self.nebs_fld
         does not exist already.

        Parameters
        ----------
        subset : gpd.GeoDataFrame, optional
            Subset of self.objects containing only features to find neighbors
            for. The default is None, and will use the entire self.objects

        Returns
        -------
        neighbor_feats : gpd.GeoDataFrame
            GeoDataFrame containing one row per neighbor for each row in
            subset. Will contain repeated geometries if features in subset
            share neighbors.
        """
        neb_src_fld = 'neighbor_src'
        neb_id_fld = 'neighbor_id'

        # Compute for entire dataframe if subset is not provided.
        if not isinstance(subset, (gpd.GeoDataFrame, pd.DataFrame)):
            # TODO: Turn subset into an ImageObjects, then get subset.objects
            # SubObjects = copy.deepcopy(self)
            subset = copy.deepcopy(self.objects)

        # Find neighbors if column containing neighbor IDs does not already
        # exist
        if self.nebs_fld not in subset.columns:
            self.get_neighbors(subset=subset)
            subset = self.objects[self.objects.index.isin(subset.index)]

        # Store source IDs and neighbor IDs from in lists
        source_ids = []
        neighbor_ids = []
        for index, row in tqdm(subset.iterrows(),
                               desc='Getting neighbor features'):
            # Get all neighbors of current feature, as list, add to master list
            neighbors = row[self.nebs_fld]
            neighbor_ids.extend(neighbors)
            # Add source ID to list one time for each of its neighbors
            for n in neighbors:
                source_ids.append(index)

        # Create 'look up' dataframe of with one row for each source id and
        # neighbor pair
        src_lut = pd.DataFrame({neb_src_fld: source_ids, neb_id_fld: neighbor_ids})

        # Find each neighbor feature in the master GeoDataFrame,
        # creating a new GeoDataFrame
        neighbor_feats = gpd.GeoDataFrame()
        for ni in neighbor_ids:
            # feat = self.objects[self.objects[unique_id] == ni]
            feat = self.objects.loc[[ni]]
            neighbor_feats = pd.concat([neighbor_feats, feat])

        # Join neighbor features to sources
        # This is one-to-many with one row for each neighbor-source pair
        neighbor_feats = pd.merge(neighbor_feats, src_lut,
                                  left_index=True, right_on=neb_id_fld)
        # Remove redundant neighbor_id column - this is the same as the index
        # in this df
        neighbor_feats.drop(columns=[neb_id_fld], inplace=True)

        return neighbor_feats

    def compute_neighbor_values(self, value_field, subset=None,
                                compute_neighbors=False):
        """Look up the value in value field for each neighbor,
        adding a dict of {neighbor_id: value} in out_field of
        each row. If compute_neighbors == False, only performed
        on rows where neighbors have been computed previously).
        Parameters
        ---------
        value_field : str
            Name of field to compute neighbor values for
        subset : pd.DataFrame or gpd.GeoDataFrame
            Subset of self.objects to compute neighbors for
            TODO: Change all "subsets" to take list of indicies to compute on
             which will avoid duplicating large dataframes
        compute_neighbors : bool
            True to compute neighbor for any object in subset (or self.objects
             if subset not provided) that doesn't have neighbors computed
        """
        out_field = self._nv_field_name(value_field)
        if subset is None:
            subset = copy.deepcopy(self.objects)
        if compute_neighbors:
            # If subset doesn't have neighbors computed, compute them
            if any(subset[self.nebs_fld].isnull()):
                subset = self.get_neighbors(subset)
        # Get all neighbors that have been found in dataframe
        # This takes lists of neighbors and puts them into a Series,
        # drops NaN's and drops duplicates.
        neighbors = pd.DataFrame(subset.neighbors.explode().
                                 dropna().
                                 drop_duplicates()).set_index(self.nebs_fld)
        # Get the value in value_field for each neighbor feature
        neighbors = pd.merge(neighbors, self.objects[[value_field]],
                             left_index=True, right_index=True,
                             )
        # Create a dictionary in the main objects dataframe
        # which is {neighbor_id: value} for all objects that
        # have neighbors computed
        # TODO: change to use get_value()
        subset[out_field] = (subset[~subset[self.nebs_fld].isnull()][self.nebs_fld]
                             .apply(lambda x: {i: neighbors.at[i, value_field]
                                               for i in x}))

        # Merge neighbor value field back in
        if out_field in self.fields:
            self.objects.drop(columns=out_field, inplace=True)
        self.objects = pd.merge(self.objects,
                                subset[[out_field]],
                                how='outer', suffixes=('', '_y'),
                                left_index=True, right_index=True)
        # Add neighbor value field and field it is based on to list of tuples
        # of all neighbor value fields
        self.nv_fields.append((value_field, out_field))

        return self.objects[self.objects.index.isin(subset.index)]

    def merge_seeds(self, rules):
        """Find objects to use as merge seeds based on the passed rules

        Parameters
        ---------
        rules : list
            List of dictionaries of kwa to pass to apply_single_rule:
            {rule_type: '', in_field: '', op: '', threshold: ''}

        Returns
        -------
        List of IDs to use as merge seeds.
        """
        # Get a single series indicating if all conditions are True across each
        # row
        is_merge_seed = (pd.DataFrame([self.apply_single_rule(**kwargs)
                                       for kwargs in rules])
                         .transpose()
                         .all(axis=1))
        self.objects[self.m_seed_fld] = is_merge_seed

        return is_merge_seed.index

    def update_mergeable_ids(self, max_iter):
        """Returns list of IDs that are True in the specified fields"""
        if max_iter:
            self.objects[self.continue_iter] = (self.objects[self.m_ct_fld] <
                                                max_iter)

        self.mergeable_ids = list(self.objects[
                                       self.objects[[self.m_seed_fld,
                                                     self.mc_fld,
                                                     self.m_fld,
                                                     self.continue_iter]]
                                  .all(axis='columns') == True]
                                  .index)
        return self.mergeable_ids

    def find_merge_candidates(self, fields_ops_thresholds):
        """
        Marks columns that meet each merge criteria (field op threshold)
        in fields_ops_thresholds, as True in mc_fld field.

        Parameters
        ---------
        fields_ops_thresholds : list
            List of tuples of (field_name, operator fxn, threshold)

        Returns
        ------
        None : updates self.objects in place
        """
        # Add merge candidate field does not exist
        if self.mc_fld not in self.fields:
            self.objects[self.mc_fld] = None

        if fields_ops_thresholds is not None:
            df = pd.DataFrame(
                [op(self.objects[field], threshold) for field, op, threshold in
                 fields_ops_thresholds]).transpose()
            # If an objects has already been marked unmergeable, mark it so again
            df[self.mc_fld] = self.objects[self.mc_fld]\
                .apply(lambda x: x is not False)
            self.objects[self.mc_fld] = df.all(axis='columns')
        else:
            self.objects[self.mc_fld] = True

    def pseudo_merging(self, mc_fields_ops_thresholds, pairwise_criteria,
                       grow_fields: list = None,
                       merge_seeds=False,
                       max_iter=None):
        """
        mc_fields_ops_thresholds : list
            List of tuples of (field_name, operator fxn, threshold) to identify
            merge candidate objects. Only these object will be merged into. If
            None, all objects are candidates for merging
        grow_fields : list
            List of field names to base growing on. The neighbor with the
            closest value(s) in these fields will be merged first
        pairwise_criteria : dict
        Dict of critiria, supported types:
            'within': {'field': "field_name", 'range': "within range"}
            'threshold: {'field': "field_name, 'op', operator comparison fxn,
                         'threshold': value to use in fxn}
            Note: 'theshold' can be 'self' to compare to each objects own
                value in the field being considered
        merge_seeds : bool
            True if merge_seeds have been computed and should be used, if not
            all objects are considered merge seeds.
        max_iter : int
            Number of merges allowed for a given feature, if None,
            merging will only cease once no neighbor match criteria
        """
        logger.info('Beginning pseudo-merge to determine merges...')
        # Initiate count of merges per object
        self.objects[self.m_ct_fld] = 0

        # Get objects that meet merge criteria
        self.find_merge_candidates(mc_fields_ops_thresholds)

        logger.debug('Merge candidates found: {:,}'.format(
            len(self.objects[self.objects[self.mc_fld] == True])))

        # Sort by area
        self.objects = self.objects.sort_values(by=self.area_fld)

        # Set all objects as possibly mergeable, this field is later used to
        # mark features that have been checked and no merge found as no longer
        # mergeable
        self.objects[self.m_fld] = True

        # Check if merge_seeds provided, if not mark all objects as seeds
        if not merge_seeds:
            self.objects[self.m_seed_fld] = True
        logger.debug('Merge seeds found: '
                     '{}'.format(len(self.objects[self.objects[self.m_seed_fld]])))

        # Initialize empty lists to store "merge path" -> ordered list of
        # neighbors IDs to merge into
        self.objects[self.mp_fld] = [[] for i in range(self.num_objs)]

        # Determine all mergable IDs
        if max_iter is None:
            self.objects[self.continue_iter] = False
        self.update_mergeable_ids(max_iter=max_iter)

        # Get neighbors for mergeable IDs
        self.get_neighbors(subset=self.objects[
            self.objects.index.isin(self.mergeable_ids)])

        # If no grow fields provided, use all value fields
        if grow_fields is None:
            grow_fields = self.value_fields

        # While there are rows that are merge_seeds and
        # that haven't been checked and there are merge_candidates,
        # look for a possible merge to a neighbor
        while self.mergeable_ids:
            logger.debug('Mergeable IDs: {}'.format(len(self.mergeable_ids)))
            # Get the first row that is all of:
            # merge_seed, merge_candidate, marked mergeable, not at max_iter
            r = self.objects.loc[self.mergeable_ids[0]]

            # Get ID of row
            i = r.name

            # logger.debug('Current ID: {}'.format(i))
            # Check that neighbor value fields have been computed for all
            # merge fields, if not compute
            for gf in grow_fields:
                merge_nv_field = self._nv_field_name(gf)
                # TODO: better check for computed_neighbor values
                # TODO: could add compute_neighbors=True to compute neighbors
                #  "on the fly" rather than upfront out of the merging loop
                # if not all(neighbors in merge_field.keys()) (apply)
                if merge_nv_field not in r.index:
                    self.compute_neighbor_values(gf)

            # Find best match, which is closest value in terms of standard
            # deviations summed for all merge fields, given pairwise criteria
            # are all met
            best_match = None

            # Init dict to hold all standard deviations for current ID for
            # each neighbor:
            # {neighbor_id1: [std of merge_field1, std of merge_field2, ...],
            #  neighbor_id2: [...]}
            neighbor_abs_stds = {n: [] for n in r[self.nebs_fld]
                                 if n is not None}
            # Compute number of std away from current row's value for each
            # neighbor for each merge_field, in order to choose best neighbor
            # to merge with
            for neb_id in r[self.nebs_fld]:
                # Skip if marked unmergeable
                if not self.objects.at[neb_id, self.m_fld]:
                    continue
                # Get the neighbors row, containing all values
                possible_match = self.objects.loc[neb_id, :]
                # Check if neighbor meets pairwise criteria, if not skip
                if pairwise_criteria is not None and \
                        not all([pairwise_match(r, possible_match, pc)
                                 for pc in pairwise_criteria]):
                    neighbor_abs_stds.pop(neb_id)
                    continue
                # Get number of standard deviations
                for gf in grow_fields:
                    neighbor_abs_stds[neb_id].append(
                        abs_stds(r[gf], possible_match[gf],
                                 std=self.object_stats.loc['std', gf]))

            # Find neighbor with least total std away from feature considering
            # all merge fields
            best_match_id = None
            if len(neighbor_abs_stds.keys()) != 0:
                best_match_id = min(neighbor_abs_stds.keys(),
                                    key=lambda k: sum(neighbor_abs_stds[k]))
                best_match = self.objects.loc[best_match_id, :]
                logger.debug('Match found: {}'.format(best_match_id))

            if best_match is not None:
                # Update value fields of best match row with approriate
                # aggregate se.g.: weighted mean
                for vf, agg_type in self.value_fields.items():
                    if agg_type == 'mean':
                        self.objects.at[best_match_id, vf] = (
                            weighted_mean(values=[r[vf], best_match[vf]],
                                          weights=[r[self.area_fld],
                                                   best_match[self.area_fld]]))
                    elif agg_type == 'majority':
                        # Get the value assoc with object that has most area
                        self.objects.at[best_match_id, vf] = (
                            max([(r[vf], r[self.area_fld]),
                                 (best_match[vf], best_match[self.area_fld])],
                                key=operator.itemgetter(1))[0])
                    elif agg_type == 'minority':
                        # Get the value assoc. with object that has least area
                        self.objects.at[best_match_id, vf] = (
                            min([(r[vf], r[self.area_fld]),
                                 (best_match[vf], best_match[self.area_fld])],
                                key=operator.itemgetter(1))[0])
                    elif agg_type == 'minimum':
                        self.objects.at[best_match_id, vf] = min(
                            r[vf], best_match[vf])
                    elif agg_type == 'maximum':
                        self.objects.at[best_match_id, vf] = max(
                            r[vf], best_match[vf])
                    elif agg_type == 'sum':
                        self.objects.at[best_match_id, vf] = sum(
                            r[vf] + best_match[vf])
                    else:
                        logger.error('Unknown agg_type: {} for '
                                     'value field: {}'.format(agg_type, vf))

                # Update area field (add areas) TODO: make pseudo_area field
                self.objects.at[best_match_id, self.area_fld] = (
                        r[self.area_fld] + best_match[self.area_fld])

                # Calculate neighbors for best match if not already
                if not isinstance(best_match[self.nebs_fld], (list, pd.Series)):
                    if pd.isnull(best_match[self.nebs_fld]):
                        self.get_neighbors(
                            self.objects[
                                self.objects.index.isin([best_match_id])])

                # Replace current object with best match in all neighbor fields
                # and merge_paths
                self.replace_neighbor(i, best_match_id, update_merges=True)

                # Update neighbor value fields that had current object as
                # neighbor
                for vf, nvf in self.nv_fields:
                    self.replace_neighbor_value(nvf, i, best_match_id,
                                                self.objects.at[best_match_id,
                                                                vf])
                # Update merge_path
                # Get all of the feature to be merged's merge_path ids and add
                # them to best match objects merge_path
                # TODO ensure not adding self to merge_path
                self.objects.at[best_match_id, self.mp_fld].extend(
                    r[self.mp_fld])
                # Store ID to merge in new (best_match) object's merge_path
                # field
                self.objects.at[best_match_id, self.mp_fld].append(i)

                # Mark as merge_seed
                self.objects.at[best_match_id, self.m_seed_fld] = True

            # Mark original feature as no longer mergeable, it was either
            # "merged" or there was no possible match
            self.objects.at[i, self.mp_fld] = []
            self.objects.at[i, self.m_fld] = False
            self.objects.at[i, self.mc_fld] = False
            # self.print_info()

            self.merge()

            # Recalculate merge candidates, using new (merged) values
            self.find_merge_candidates(mc_fields_ops_thresholds)

            # Resort by area so smallest possible merge object is checked next
            self.objects = self.objects.sort_values(by=self.area_fld)

            self.update_mergeable_ids(max_iter=max_iter)
            # print('Best match ID: {}'.format(best_match_id))

            # logger.debug('\n{}'.format(self.objects[
            #     self.objects[self.mp_fld].apply(lambda x: len(x) > 0)][self.mp_fld]))

    def merge(self):
        # merge features that have a merge path
        logger.debug('Performing calculated merges...')
        logger.debug('Objects before merge: {:,}'.format(self.num_objs))
        for i, r in self.objects[
                self.objects[self.mp_fld].map(lambda d: len(d)) > 0].iterrows():
            logger.debug('Merging: {} to {}'.format(i, r[self.mp_fld]))
            # Create gdf of current row and the features to merge with it.
            # Important that the current row is first, as it contains the
            # correct aggregated values and the dissolve function defaults
            # to keeping the first rows values
            to_merge = pd.concat([gpd.GeoDataFrame([r]), self.objects[
                self.objects.index.isin(r[self.mp_fld])]])
            to_merge['temp'] = 1
            to_merge = to_merge.dissolve(by='temp')
            to_merge.index = [i]
            # Zero out merge_path
            to_merge[self.mp_fld] = [[]]
            # Add to merge count
            to_merge[self.m_ct_fld] = to_merge[self.m_ct_fld] + len(r[self.mp_fld])
            # TODO: confirm whether it is there or not
            if 'temp' in to_merge.columns:
                to_merge.drop(columns='temp', inplace=True)
            # Drop both original objects
            self.objects.drop(r[self.mp_fld] + [i], inplace=True)
            # Add merged object back in
            self.objects = pd.concat([self.objects, to_merge])
        # logger.debug('Objects after merge: {:,}'.format(self.num_objs))

    # def determine_adj_thresh(self, neb_values_fld, value_thresh, value_op, out_field, subset=None):
    #     """Determines if each row is has neighbor that meets the value
    #     threshold provided. Used for classifying.
    #
    #     Parameters
    #     ---------
    #     neb_values_fld : str
    #         Field containing dict of {neighbor_id: value}
    #     value_thresh : str/int/float/bool
    #         The value to compare each neighbors value to.
    #     value_op : operator function
    #         From operator library, the function to use to compare neighbor
    #         value to value_thresh:
    #         operator.le(), operator.gte(), etc.
    #     out_field : str
    #         Field to create in self.objects to store result of adjacency test.
    #
    #     Returns
    #     --------
    #     None : modifies self.objects in place
    #     """
    #     # For all rows where neighbor_values have been computed, compare
    #     # neighbor values to value_thresh using the given value_op. If any are
    #     # True, True is returned
    #     self.objects[out_field] = (self.objects[
    #                     ~self.objects[neb_values_fld].isnull()][neb_values_fld]
    #                     .apply(lambda x:
    #                            any(value_op(v, value_thresh)
    #                                for v in x.values())))

    def best_adjacent_to(self, in_field, op):
        best_fxn_lut = {
            operator.lt: min,
            operator.le: min,
            operator.gt: max,
            operator.ge: max
        }
        best_fxn = best_fxn_lut[op]

        logger.debug('Finding adjacent features with values in {}...'.format(in_field))
        # Create neighbor-value field(s) if necessary
        in_field_nv = self._nv_field_name(in_field)
        if in_field_nv not in self.fields:
            self.compute_neighbor_values(in_field)

        # Get tuple of (ID, value) of "best" neighbor
        best_series = self.objects[in_field_nv].apply(
            lambda nv: best_fxn(nv.items(), key=operator.itemgetter(1))
            if pd.notnull(nv) else nv)

        return best_series

    def adjacent_to(self, in_field, op, threshold,
                    src_field=None, src_op=None, src_thresh=None,
                    out_field=None,
                    compute_neighbors=True):
        logger.debug('Finding adjacent features with values...')

        # # Create neighbor-value field(s) if necessary
        in_field_nv = self._nv_field_name(in_field)
        if in_field_nv not in self.fields:
            self.compute_neighbor_values(in_field,
                                         compute_neighbors=compute_neighbors)

        if src_field:
            adj_series = (
                # src object threshold
                (src_op(self.objects[src_field], src_thresh)) &
                # True if any neighbor has value that meets op(nv, threshold)
                (self.objects[in_field_nv].apply(
                    lambda nv: any([op(v, threshold) for k, v in nv.items()])
                    if pd.notnull(nv) else nv))
                )
        else:
            adj_series = (self.objects[in_field_nv].apply(
                lambda nv: any([op(v, threshold) for k, v in nv.items()])
                if pd.notnull(nv) else nv))

        if out_field:
            self.objects[out_field] = adj_series

        return adj_series

    def write_objects(self, out_objects, to_str_cols=None, overwrite=False, **kwargs):
        # Create list of columns to write as strings rather than lists, tuples
        if not to_str_cols:
            to_str_cols = []

        list_cols = [self.nebs_fld, self.mp_fld]
        for lc in list_cols:
            if lc in self.fields:
                to_str_cols.append(lc)

        to_str_cols.extend([nvf for vf, nvf in self.nv_fields])

        logger.info('Writing objects to: {}'.format(out_objects))
        if self.objects.index.name in self.fields:
            self.objects.index.name = self.objects.index.name + \
                                      str(np.random.randint(0, 100))
        write_gdf(self.objects.reset_index(), out_objects,
                  to_str_cols=to_str_cols,
                  overwrite=overwrite,
                  **kwargs)

    def apply_single_rule(self, rule_type, in_field, op, threshold,
                          out_field=None, **kwargs):
        """
        Apply rule to objects, returning boolean series indicating if each
        object meets the rule.

        Parameters
        ---------
        rule_type : str
            The type of rule to apply, one of: 'threshold', 'adjacent'
        in_field : str
            The name of the field to apply the rule to
        op : operator function
            Operator to use in rule, one of operator.[lt, gt, le, ge, eq]
        threshold : float, int, str
            The value to compare in_field to using op.
        out_field : str
            The field to store the boolean results of the rule in.
        **kwargs : dict
            Keyword arguments to pass through to sub functions
            For rule_type == 'adjacent', these can be
                src_field, src_value, src_threshold
                to subset the objects that the adjacency rule is
                computed for.
        """
        # Ensure rule type is supported
        accepted_rule_types = ['threshold', 'adjacent']
        if rule_type not in accepted_rule_types:
            logger.error('Rule type: "{}" not recognized. Must be one of: '
                         '{}'.format(rule_type, accepted_rule_types))
            raise Exception

        if rule_type == 'threshold':
            results = op(self.objects[in_field], threshold)

        elif rule_type == 'adjacent':
            results = self.adjacent_to(in_field=in_field,
                                       op=op,
                                       threshold=threshold,
                                       **kwargs)
        if out_field:
            self.objects[out_field] = results
            # TODO: Add out_field to self.contraint_fields
            self.rule_fields.append(out_field)

        return results

    def apply_rules(self, rules, out_field=None):
        """
        Apply a number of rules to objects.

        Parameters
        ----------
        rules : list
            List of dictionaries of keyword arguments to single_rule

        Returns
        ---------
        pd.series : Boolean series indicating if all rules met
        """
        # Get and store boolean Series for each rule
        all_results = []
        for r in rules:
            sr = self.apply_single_rule(**r)
            all_results.append(sr)

        # Get single series indicating if True across all result rows
        # FIXME: row of NaNs being returned as True
        results = pd.DataFrame(all_results).transpose().apply(
            lambda row: all([v for v in row]), axis=1)

        if out_field:
            self.objects[out_field] = results

        return results

    def classify_objects(self, class_name,
                         threshold_rules=None,
                         adj_rules=None,
                         overwrite_class=False):
        """Classify objects according to rules passed. The class_name will
        be placed in the 'class' field of objects. If overwrite_class is
        False, any existing values in the 'class' field will be maintained
        and only objects with a Null class will be classified."""
        # TODO: optimize so that only unclassified objects have the other
        #  rules applied, as adjacency rules can take a while
        # Create class field if it doesn't exist
        if self.class_fld not in self.fields:
            self.objects[self.class_fld] = None

        # Get boolean series for each rule
        all_results = []
        if threshold_rules:
            thresholds_results = self.apply_rules(threshold_rules)
            all_results.append(thresholds_results)
        if adj_rules:
            if threshold_rules:
                self.get_neighbors(subset=self.objects.loc[thresholds_results])
            for r in adj_rules:
                self.compute_neighbor_values(r[self._rule_fld_name])
            adj_results = self.apply_rules(adj_rules)
            all_results.append(adj_results)

        update_rows = pd.DataFrame(all_results).transpose().apply(
            lambda row: all([v for v in row]), axis=1)

        # Add class name to rows that meet criteria
        if overwrite_class:
            # Update all rows that meet rules
            logger.info("Classifying {:,} objects".format(
                len(self.objects.loc[update_rows, self.class_fld])))
            self.objects.loc[update_rows, self.class_fld] = class_name
        else:
            # Update only rows that meet rules AND are not classified
            logger.info("Classifying {:,} objects".format(
                len(self.objects.loc[
                update_rows & self.objects[self.class_fld].isnull(),
                self.class_fld])))
            self.objects.loc[
                update_rows & self.objects[self.class_fld].isnull(),
                self.class_fld] = class_name
