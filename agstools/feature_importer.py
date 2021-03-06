import logging
from copy import deepcopy
from agstools.feature_processor import FeatureProcessor
from agstools.attribute_mapper import AttributeMapper
from agstools.utility import merge_dicts

logger = logging.getLogger(__name__)


class FeatureImporter(object):
    """Import features from source feature layer to target feature layer."""

    def __init__(self, src_feat_layer, tgt_feat_layer, custom_attr_mapper=None):
        """
        Class initializer.

        :param src_feat_layer: <feature_layer.FeatureLayer> Source feature layer
        :param tgt_feat_layer: <feature_layer.FeatureLayer> Target feature layer
        :param custom_attr_mapper: <attribute_mapper.AttributeMapper> Source to Target attribute mapper
        """

        self.src_feat_layer = src_feat_layer
        self.tgt_feat_layer = tgt_feat_layer
        self.cust_attr_mapper = custom_attr_mapper if isinstance(custom_attr_mapper, AttributeMapper) else AttributeMapper()
        self.auto_attr_mapper = self.__build_auto_attr_mapper()
        self.comp_features = {'src': {'index': {}, 'matched': [], 'unmatched': []},
                              'tgt': {'index': {}, 'matched': [], 'unmatched': []}}

    def __build_auto_attr_mapper(self):
        """
        Return attribute mapper for matching source and target field names.

        :return: <attribute_mapper.AttributeMapper> Attribute mapper
        """

        auto_mapper = AttributeMapper()
        src_fields = [f['name'] for f in self.src_feat_layer.definition()['fields']]
        tgt_fields = [f['name'] for f in self.tgt_feat_layer.definition()['fields']]

        for f_name in list(set(src_fields) & set(tgt_fields)):
            auto_mapper.add_mapping(f_name, f_name)

        return auto_mapper

    def __reset_comp_features(self):
        """
        Clear out the feature comparison results.

        :return: None
        """

        self.comp_features = {'src': {'index': {}, 'matched': [], 'unmatched': []},
                              'tgt': {'index': {}, 'matched': [], 'unmatched': []}}

    def __comp_features(self, src_uid_field, tgt_uid_field):
        """
        Calculate and set feature comparison results.

        :param src_uid_field: <str> Source unique ID field name
        :param tgt_uid_field: <str> Target unique ID field name
        :return: None
        """

        # remove previously compared features
        self.__reset_comp_features()

        # get current attribute map
        attr_map = self.__get_attr_map()

        # get oid field names
        src_oid_field = self.src_feat_layer.definition()['objectIdField']
        tgt_oid_field = self.tgt_feat_layer.definition()['objectIdField']

        # get source feat layer attributes from attr map
        src_attr = [k for k, v in sorted(attr_map.items())]
        # get target feat layer attributes from attr map
        tgt_attr = [v for k, v in sorted(attr_map.items())]

        # get source and target json features
        src_features = self.src_feat_layer.query_features_batch(where='1=1', outFields=', '.join(src_attr))
        tgt_features = self.tgt_feat_layer.query_features_batch(where='1=1', outFields=', '.join(tgt_attr))

        # build feature indexes with uid field as key, oid field as value
        self.comp_features['src']['index'] = {f['attributes'][src_uid_field]: f['attributes'][src_oid_field]
                                              for f in src_features}
        self.comp_features['tgt']['index'] = {f['attributes'][tgt_uid_field]: f['attributes'][tgt_oid_field]
                                              for f in tgt_features}

        # process matched and unmatched features from source and target feature sets
        self.comp_features['src']['matched'] = [
            f for f in src_features if f['attributes'][src_uid_field] in self.comp_features['tgt']['index']]
        self.comp_features['src']['unmatched'] = [
            f for f in src_features if f['attributes'][src_uid_field] not in self.comp_features['tgt']['index']]
        self.comp_features['tgt']['matched'] = [
            f for f in tgt_features if f['attributes'][tgt_uid_field] in self.comp_features['src']['index']]
        self.comp_features['tgt']['unmatched'] = [
            f for f in tgt_features if f['attributes'][tgt_uid_field] not in self.comp_features['src']['index']]

    def __get_attr_map(self):
        """
        Return current, combined attribute map

        Custom attribute mapper fields override auto attribute mapper fields.

        :return: <dict> Attribute map
        """

        return merge_dicts(self.auto_attr_mapper.attribute_map, self.cust_attr_mapper.attribute_map)

    def import_features(self, src_uid_field, tgt_uid_field):
        """
        Import features from source to target and delete features from source.

        Feature in source not in target: feature added to target and deleted from source
        Feature in target not in source: feature ignored in target
        Feature in source and target: feature ignored in target and deleted from source

        :param src_uid_field: <str> Source unique ID field name
        :param tgt_uid_field: <str> Target unique ID field name
        :return: None
        """

        self.__comp_features(src_uid_field, tgt_uid_field)

        # get current attribute map
        attr_map = self.__get_attr_map()

        # get oid field names
        src_oid_field = self.src_feat_layer.definition()['objectIdField']
        tgt_oid_field = self.tgt_feat_layer.definition()['objectIdField']

        # features that have not been imported
        add_features = deepcopy(self.comp_features['src']['unmatched'])
        # features that were previously imported but never deleted
        old_features = deepcopy(self.comp_features['src']['matched'])

        logger.debug("Adding target features ({0}).".format(len(add_features)))
        if len(add_features) > 0:
            # get OID values for add features to delete from source
            add_oids = [f['attributes'][src_oid_field] for f in add_features]
            add_oids_str = ', '.join([str(o) for o in add_oids])
            # create a feature processor to modify add features
            add_fp = FeatureProcessor(add_features)
            # remap field names
            add_fp.replace_attributes(attr_map)
            # remove OID field (auto-generated on insert via REST addFeatures operation)
            add_fp.remove_attributes([src_oid_field])
            # add features to target feature layer
            self.tgt_feat_layer.add_features_batch(features=add_fp.features)
            # delete features from source feature layer
            logger.debug("Deleting source features ({0}).".format(len(add_oids)))
            self.src_feat_layer.delete_features(objectIds=add_oids_str)
        if len(old_features) > 0:
            # get OID values for old features to delete from source
            old_oids = [f['attributes'][src_oid_field] for f in old_features]
            old_oids_str = ', '.join([str(o) for o in old_oids])
            # delete features from source feature layer
            logger.debug("Deleting stale source features ({0}).".format(len(old_oids)))
            self.src_feat_layer.delete_features(objectIds=old_oids_str)
