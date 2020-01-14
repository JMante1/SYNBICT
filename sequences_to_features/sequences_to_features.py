import logging
import argparse
import os
import sys
import requests
import json

from Bio.Seq import Seq
from sbol import *
from flashtext import KeywordProcessor

def load_sbol(sbol_file):
    logging.info('Loading %s', sbol_file)

    doc = Document()
    doc.read(sbol_file)

    doc.name = sbol_file

    doc.addNamespace('http://purl.org/dc/elements/1.1/', 'dc')
    doc.addNamespace('http://wiki.synbiohub.org/wiki/Terms/igem#', 'igem')
    doc.addNamespace('http://wiki.synbiohub.org/wiki/Terms/synbiohub#', 'sbh')

    return doc

def load_non_sbol(non_sbol_file):
    logging.info('Loading %s', non_sbol_file)

    conversion_request = {
        'options': {
            'language' : 'SBOL2',
            'test_equality': False,
            'check_uri_compliance': False,
            'check_completeness': False,
            'check_best_practices': False,
            'fail_on_first_error': False,
            'provide_detailed_stack_trace': False,
            'subset_uri': '',
            'uri_prefix': getHomespace(),
            'version': '1',
            'insert_type': False,
            'main_file_name': 'main file',
            'diff_file_name': 'comparison file'
        },
        'return_file': True,
        'main_file': open(non_sbol_file).read()
    }

    conversion_response = requests.post("https://validator.sbolstandard.org/validate/", json=conversion_request)

    response_dict = json.loads(conversion_response.content.decode('utf-8'))

    doc = Document()
    doc.readString(response_dict['result'])

    (file_base, file_extension) = os.path.splitext(non_sbol_file)
    doc.name = file_base + '.xml'

    doc.addNamespace('http://purl.org/dc/elements/1.1/', 'dc')
    doc.addNamespace('http://wiki.synbiohub.org/wiki/Terms/igem#', 'igem')
    doc.addNamespace('http://wiki.synbiohub.org/wiki/Terms/synbiohub#', 'sbh')

    return doc

class Feature():

    SO_REGION = 'http://identifiers.org/so/SO:0000001'
    SO_SEQUENCE_FEATURE = 'http://identifiers.org/so/SO:0000110'

    GENERIC_ROLES = {
        SO_REGION,
        SO_SEQUENCE_FEATURE
    }

    def __init__(self, nucleotides, identity, roles, sub_identities=[], parent_identities=[]):
        self.nucleotides = nucleotides
        self.identity = identity
        self.sub_identities = sub_identities
        self.parent_identities = parent_identities
        self.roles = roles

    def reverse_complement_nucleotides(self):
        return str(Seq(self.nucleotides).reverse_complement())

    @classmethod
    def has_non_generic_role(cls, roles):
        return len(roles.difference(cls.GENERIC_ROLES)) > 0

    @classmethod
    def has_same_roles(cls, roles1, roles2):
        return len(roles1.difference(roles2)) == 0

    def is_non_generic(self):
        return self.has_non_generic_role(self.roles)

    def is_same_as(self, other_feature):
        return self.has_same_roles(self.roles, other_feature.roles)

class FeatureLibrary():

    def __init__(self, docs, require_sequence=True):
        self.features = []
        self.docs = docs
        self.__feature_map = {}

        logging.info('Loading features')

        for i in range(0, len(self.docs)):
            self.__load_features(self.docs[i], i, require_sequence)

    def update(self, require_sequence=True):
        added_features = []

        for i in range(0, len(self.docs)):
            added_features.extend(self.__load_features(self.docs[i], i, require_sequence))

        return added_features

    def __load_features(self, doc, doc_index, require_sequence=True):
        loaded_features = []

        for comp_definition in doc.componentDefinitions:
            if BIOPAX_DNA in comp_definition.types and comp_definition.identity not in self.__feature_map:
                dna_seqs = self.get_DNA_sequences(comp_definition, doc)

                sub_identities = []

                for sub_comp in comp_definition.components:
                    sub_identities.append(sub_comp.definition)

                if len(dna_seqs) > 0:
                    feature = Feature(dna_seqs[0].elements, comp_definition.identity, set(comp_definition.roles),
                        sub_identities, comp_definition.wasDerivedFrom)

                    loaded_features.append(feature)
                    self.features.append(feature)

                    self.__feature_map[comp_definition.identity] = doc_index
                elif not require_sequence:
                    feature = Feature('', comp_definition.identity, set(comp_definition.roles),
                        sub_identities, comp_definition.wasDerivedFrom)

                    loaded_features.append(feature)
                    self.features.append(feature)

                    self.__feature_map[comp_definition.identity] = doc_index
                else:
                    logging.warning('%s not loaded since its DNA sequence was not found', comp_definition.identity)

        return loaded_features

    def get_features(self, min_feature_length=0, children_only=False):
        features = []

        if children_only:
            parent_identities = set()

            for feature in self.features:
                for parent_identity in feature.parent_identities:
                    parent_identities.add(parent_identity)

            for feature in self.features:
                if len(feature.nucleotides) > min_feature_length and feature.identity not in parent_identities:
                    features.append(feature)
        else:
            for feature in self.features:
                if len(feature.nucleotides) > min_feature_length:
                    features.append(feature)

        return features

    def get_added_feature_identities(self):
        added_feature_identities = set()

        for doc in self.docs:
            for comp_definition in doc.componentDefinitions:
                if comp_definition.identity not in self.__feature_map:
                    added_feature_identities.append(comp_definition.identity)

        return added_feature_identities

    def get_document(self, identity):
        if identity in self.__feature_map:
            return self.docs[self.__feature_map[identity]]
        else:
            return None

    def get_definition(self, identity):
        return self.get_document(identity).getComponentDefinition(identity)

    def has_feature(self, identity):
        return identity in self.__feature_map

    @classmethod
    def get_DNA_sequences(cls, comp_definition, doc):
        dna_seqs = []

        for seq_URI in comp_definition.sequences:
            try:
                seq = doc.getSequence(seq_URI)
            except RuntimeError:
                seq = None

            if seq is not None and seq.encoding == SBOL_ENCODING_IUPAC:
                dna_seqs.append(seq)

        return dna_seqs

    @classmethod
    def copy_component_definition(cls, comp_definition, source_doc, sink_doc, import_namespace=False, min_dna_length=0):
        if min_dna_length > 0:
            dna_seqs = FeatureLibrary.get_DNA_sequences(comp_definition, source_doc)

            copy_valid = len(dna_seqs) > 0 and len(dna_seqs[0].elements) >= min_dna_length
        else:
            copy_valid = True

        if copy_valid:
            namespace = '/'.join(comp_definition.identity.split('/')[:-2])

            if import_namespace:
                if namespace == getHomespace():
                    try:
                        version = int(comp_definition.version)
                    except (TypeError, ValueError):
                        return None

                    try:
                        definition_copy = comp_definition.copy(sink_doc, namespace, str(version + 1))
                    except RuntimeError:
                        return sink_doc.getComponentDefinition('/'.join([namespace, comp_definition.displayId, str(version + 1)]))
                else:
                    try:
                        definition_copy = comp_definition.copy(sink_doc, namespace, '1')
                    except RuntimeError:
                        return sink_doc.getComponentDefinition('/'.join([namespace, comp_definition.displayId, '1']))
            else:
                try:
                    sink_doc.getComponentDefinition(comp_definition.identity)
                    
                    return None
                except RuntimeError:
                    definition_copy = comp_definition.copy(sink_doc)

            definition_copy.sequences = list(comp_definition.sequences)

            for seq_anno in comp_definition.sequenceAnnotations:
                if seq_anno.component is not None:
                    sub_comp = comp_definition.components.get(seq_anno.component)

                    sub_copy = definition_copy.components.get(sub_comp.displayId)

                    anno_copy = definition_copy.sequenceAnnotations.get(seq_anno.displayId)
                    anno_copy.component = sub_copy.identity

            for sub_comp in comp_definition.components:
                sub_definition = source_doc.getComponentDefinition(sub_comp.definition)

                sub_copy = definition_copy.components.get(sub_comp.displayId)

                sub_definition_copy = cls.copy_component_definition(sub_definition, source_doc, sink_doc, import_namespace,
                    min_dna_length)

                if sub_definition_copy is not None:
                    sub_copy.definition = sub_definition_copy.identity

            if import_namespace:
                try:
                    definition_copy.setPropertyValue('http://wiki.synbiohub.org/wiki/Terms/synbiohub#ownedBy', '')
                except LookupError:
                    pass

                try:
                    definition_copy.setPropertyValue('http://wiki.synbiohub.org/wiki/Terms/synbiohub#topLevel', '')
                except LookupError:
                    pass

                definition_copy.wasGeneratedBy = []

            for seq_URI in comp_definition.sequences:
                try:
                    sink_doc.getSequence(seq_URI)
                except RuntimeError:
                    seq = source_doc.getSequence(seq_URI)

                    seq.copy(sink_doc)

            return definition_copy
        else:
            return None

class FeatureAnnotater():

    def __init__(self, feature_library, min_feature_length):
        self.feature_library = feature_library
        self.feature_matcher = KeywordProcessor()

        for feature in feature_library.features:
            inline_elements = ' '.join(feature.nucleotides)

            if self.__has_min_length(feature, min_feature_length):
                if inline_elements in self.feature_matcher:
                    if feature.is_non_generic():
                        canonical_features = [cf for cf in self.feature_matcher.get_keyword(inline_elements) if cf.is_non_generic()]

                        canonical_features.append(feature)
                else:
                    canonical_features = [feature]

                self.feature_matcher.add_keyword(inline_elements, canonical_features)

    @classmethod
    def __has_min_length(cls, feature, min_feature_length):
        return len(feature.nucleotides) >= min_feature_length

    @classmethod
    def __create_sub_component(cls, parent_definition, child_definition):
        i = 1

        while i > 0:
            try:
                sub_comp = parent_definition.components.create('_'.join([child_definition.displayId, str(i)]))
            except RuntimeError:
                sub_comp = None

            if sub_comp is None:
                i = i + 1
            else:
                sub_comp.name = child_definition.name
                sub_comp.definition = child_definition.identity

                sub_comp.roleIntegration = None

                i = -1

        return sub_comp

    @classmethod
    def __create_sequence_annotation(cls, parent_definition, child_definition, orientation, start, end,
                                     sub_comp_URI=None, parent_URI=None):
        i = 1

        while i > 0:
            try:
                seq_anno = parent_definition.sequenceAnnotations.create('_'.join([child_definition.displayId,
                                                                                  'anno',
                                                                                  str(i)]))
            except RuntimeError:
                seq_anno = None

            if seq_anno is None:
                i = i + 1
            else:
                seq_anno.name = child_definition.name
                seq_anno.description = child_definition.description
                if sub_comp_URI is not None:
                    seq_anno.component = sub_comp_URI
                if parent_URI is not None:
                    seq_anno.roles = seq_anno.roles + child_definition.roles
                    seq_anno.wasDerivedFrom = seq_anno.wasDerivedFrom + [parent_URI]
                
                location = seq_anno.locations.createRange('loc_1')

                location.orientation = orientation
                location.start = start
                location.end = end

                i = -1

        return seq_anno

    def __process_feature_matches(self, target_doc, target_definition, feature_matches, orientation, target_length,
            rc_factor=0):
        for feature_match in feature_matches:
            temp_start = feature_match[1]//2 + 1
            temp_end = (feature_match[2] + 1)//2

            if rc_factor > 0:
                start = rc_factor - temp_end
                end = rc_factor - temp_start
            else:
                start = temp_start
                end = temp_end

            for feature in feature_match[0]:
                if len(feature.nucleotides) < target_length:
                    feature_definition = self.feature_library.get_definition(feature.identity)

                    sub_comp = self.__create_sub_component(target_definition, feature_definition)
                    self.__create_sequence_annotation(target_definition, feature_definition, orientation, start, end,
                                                     sub_comp.identity)

                    feature_doc = self.feature_library.get_document(feature.identity)

                    FeatureLibrary.copy_component_definition(feature_definition, feature_doc, target_doc)

                    logging.debug('Annotated %s at [%s, %s] in %s', feature_definition.identity, start, end, target_definition.identity)

    def annotate(self, target_library, min_target_length):
        annotated_identities = []

        for target in target_library.features:
            if self.__has_min_length(target, min_target_length):
                logging.info('Annotating %s', target.identity)

                inline_elements = ' '.join(target.nucleotides)
                rc_elements = ' '.join(target.reverse_complement_nucleotides())

                inline_matches = self.feature_matcher.extract_keywords(inline_elements, span_info=True)
                rc_matches = self.feature_matcher.extract_keywords(rc_elements, span_info=True)

                if len(inline_matches) > 0 or len(rc_matches) > 0:
                    target_doc = target_library.get_document(target.identity)

                    target_definition = target_doc.getComponentDefinition(target.identity)

                    definition_copy = FeatureLibrary.copy_component_definition(target_definition, target_doc, target_doc,
                        True, min_target_length)
                    
                    if definition_copy is not None:
                        self.__process_feature_matches(target_doc, definition_copy, inline_matches, SBOL_ORIENTATION_INLINE,
                            len(target.nucleotides))
                        self.__process_feature_matches(target_doc, definition_copy, rc_matches,
                            SBOL_ORIENTATION_REVERSE_COMPLEMENT, len(target.nucleotides), len(target.nucleotides) + 1)

                        annotated_identities.append(definition_copy.identity)
                    else:
                        logging.warning('%s was not annotated because its version could not be incremented.', target.identity)

                logging.info('Finished annotating %s', target.identity)

        return annotated_identities

class FeaturePruner():

    COMMON_ROLE_DICT = {
        SO_PROMOTER: 'promoter',
        SO_CDS: 'CDS',
        SO_TERMINATOR: 'terminator'
    }

    def __init__(self, feature_library, roles=set()):
        self.feature_library = feature_library
        self.roles = roles

    @classmethod
    def __has_min_length(cls, feature, min_feature_length):
        return len(feature.nucleotides) >= min_feature_length

    @classmethod
    def __is_covered(cls, anno, cover_annos, cover_offset):
        for cover_anno in cover_annos:
            if not abs(cover_anno[0] - anno[0]) <= cover_offset or not abs(cover_anno[1] - anno[1]) <= cover_offset:
                return False

        return True

    @classmethod
    def __remove_annotations(cls, indices, annos, target_definition):
        for i in range(len(annos) - 1, -1, -1):
            if annos[i][5] is None:
                feature_identity = annos[i][2]
            else:
                feature_identity = target_definition.components.get(annos[i][5]).definition
            
            if i in indices:
                target_definition.sequenceAnnotations.remove(annos[i][2])

                if annos[i][5] is not None:
                    target_definition.components.remove(annos[i][5])

                logging.debug('Removed %s at [%s, %s] in %s', feature_identity, annos[i][0], annos[i][1], target_definition.identity)

                del annos[i]

    @classmethod
    def __get_common_role(cls, roles):
        for role in roles:
            if role in cls.COMMON_ROLE_DICT:
                return cls.COMMON_ROLE_DICT[role]

        return ''

    @classmethod
    def __select_annotations(cls, doc, target_definition, annos, ask_user=True, canonical_library=None, delete_flat=False):
        kept_indices = []

        feature_messages = []

        for i in range(0, len(annos)):
            if annos[i][5] is None:
                if annos[i][4] is None:
                    feature_ID = annos[i][3]
                else:
                    feature_ID = annos[i][4]

                feature_role = cls.__get_common_role(annos[i][6])

                if ask_user:
                    if annos[i][7] is not None and len(annos[i][7]) > 0:
                        if len(feature_role) > 0:
                            feature_messages.append('{nx}: {id} ({fi}, {ro}) at [{st}, {en}]. {de}'.format(nx=str(i), id=annos[i][2],
                                fi=feature_ID, ro=feature_role, st=annos[i][0], en=annos[i][1], de=annos[i][7]))
                        else:
                            feature_messages.append('{nx}: {id} ({fi}) at [{st}, {en}]. {de}'.format(nx=str(i), id=annos[i][2],
                                fi=feature_ID, st=annos[i][0], en=annos[i][1], de=annos[i][7]))
                    elif len(feature_role) > 0:
                        feature_messages.append('{nx}: {id} ({fi}, {ro}) at [{st}, {en}]'.format(nx=str(i), id=annos[i][2],
                            fi=feature_ID, ro=feature_role, st=annos[i][0], en=annos[i][1]))
                    else:
                        feature_messages.append('{nx}: {id} ({fi}) at [{st}, {en}]'.format(nx=str(i), id=annos[i][2],
                            fi=feature_ID, st=annos[i][0], en=annos[i][1]))
                elif not delete_flat:
                    kept_indices.append(i)
            else:
                feature_identity = target_definition.components.get(annos[i][5]).definition

                if canonical_library is not None and canonical_library.has_feature(feature_identity):
                    kept_indices.append(i)
                elif ask_user:
                    feature_definition = doc.getComponentDefinition(feature_identity)
                    
                    if feature_definition.name is None:
                        feature_ID = feature_definition.displayId
                    else:
                        feature_ID = feature_definition.name

                    feature_role = cls.__get_common_role(feature_definition.roles)

                    feature_description = feature_definition.description

                    if feature_description is not None and len(feature_description) > 0:
                        if len(feature_role) > 0:
                            feature_messages.append('{nx}: {id} ({fi}, {ro}) at [{st}, {en}]. {de}'.format(nx=str(i), id=feature_identity,
                                fi=feature_ID, ro=feature_role, st=annos[i][0], en=annos[i][1], de=feature_description))
                        else:
                            feature_messages.append('{nx}: {id} ({fi}) at [{st}, {en}]. {de}'.format(nx=str(i), id=feature_identity,
                                fi=feature_ID, st=annos[i][0], en=annos[i][1], de=feature_description))
                    elif len(feature_role) > 0:
                        feature_messages.append('{nx}: {id} ({fi}, {ro}) at [{st}, {en}]'.format(nx=str(i), id=feature_identity,
                            fi=feature_ID, ro=feature_role, st=annos[i][0], en=annos[i][1]))
                    else:
                        feature_messages.append('{nx}: {id} ({fi}) at [{st}, {en}]'.format(nx=str(i), id=feature_identity,
                            fi=feature_ID, st=annos[i][0], en=annos[i][1]))

        if ask_user:
            if target_definition.name is None:
                target_ID = target_definition.displayId
            else:
                target_ID = target_definition.name

            select_message = 'There appear to be redundant features in {pi}:\n{fm}\nPlease select which ones to remove if any (comma-separated list of indices, for example 0,2,5):\n'.format(fm='\n'.join(feature_messages), pi=target_ID)

            selected_message = input(select_message)

            try:
                selected_indices = [int(si.strip()) for si in selected_message.split(',')]
            except ValueError:
                selected_indices = []

            return set(selected_indices)
        else:
            return set(range(0, len(annos))).difference(set(kept_indices))

    def __filter_annotations(self, annos, target_definition):
        for i in range(len(annos) - 1, -1, -1):
            if annos[i][5] is None:
                feature_identity = annos[i][2]

                feature_roles = annos[i][6]
            else:
                feature_identity = target_definition.components.get(annos[i][5]).definition

                feature_roles = set(self.feature_library.get_definition(feature_identity).roles)

            if len(feature_roles.intersection(self.roles)) == 0:
                target_definition.sequenceAnnotations.remove(annos[i][2])

                if annos[i][5] is not None:
                    target_definition.components.remove(annos[i][5])

                logging.debug('Removed %s at [%s, %s] in %s', feature_identity, annos[i][0], annos[i][1], target_definition.identity)

                del annos[i]

    # def __merge_annotations(self, anno, sub_anno, target_definition):
    #     feature_identity = target_definition.components.get(sub_anno[5]).definition

    #     feature_definition = self.feature_library.get_definition(feature_identity)

    #     if not Feature.has_non_generic_role(anno[6]) or Feature.has_same_roles(anno[6], set(feature_definition.roles)):
    #         seq_anno = target_definition.sequenceAnnotations.get(anno[2])

    #         seq_anno.roles = []
    #         seq_anno.component = sub_anno[5]

    #         target_definition.sequenceAnnotations.remove(sub_anno[2])

    #         logging.debug('Merged %s at [%s, %s] and %s at [%s, %s] in %s', anno[2], anno[0], anno[1], feature_identity, sub_anno[0], sub_anno[1], target_definition.identity)

    # @classmethod
    # def __get_annotations(cls, doc, comp_definition):
    #     cut_annos = [(sa.locations.getCut().at, sa.locations.getCut().at, sa.identity, sa.displayId, sa.name, sa.component, set(sa.roles)) for sa in comp_definition.sequenceAnnotations if len(sa.locations) == 1 and sa.locations[0].getTypeURI() == SBOL_CUT]
    #     annos = [(sa.locations.getRange().start, sa.locations.getRange().end, sa.identity, sa.displayId, sa.name, sa.component, set(sa.roles)) for sa in comp_definition.sequenceAnnotations if len(sa.locations) == 1 and sa.locations[0].getTypeURI() == SBOL_RANGE]
            
    #     annos.extend(cut_annos)

    #     for sub_comp in comp_definition.components:
    #         try:
    #             sub_definition = doc.getComponentDefinition(comp_definition.identity)
    #         except RuntimeError:
    #             sub_definition = None

    #         if sub_definition is not None:
    #             annos.extend(cls.__get_annotations(doc, sub_definition))

    #     return annos

    @classmethod
    def __get_flat_annotation_indices(self, anno_group):
        flat_indices = []

        for i in range(0, len(anno_group)):
            if anno_group[i][5] is None:
                flat_indices.append(i)

        return flat_indices

    def clean(self, feature_library, annotated_features, annotating_features):
        logging.info('Cleaning up')

        sub_definitions = set()

        for annotated_feature in annotated_features:
            annotated_doc = feature_library.get_document(annotated_feature.identity)
            annotated_definition = annotated_doc.getComponentDefinition(annotated_feature.identity)

            sub_IDs = set()
            temp_sub_definitions = set()

            for comp in annotated_definition.components:
                sub_IDs.add(comp.displayId)
                temp_sub_definitions.add(comp.definition)
            for seq_anno in annotated_definition.sequenceAnnotations:
                sub_IDs.add(seq_anno.displayId)

            parent_sub_IDs = set()

            for parent_identity in annotated_definition.wasDerivedFrom:
                parent_doc = feature_library.get_document(parent_identity)

                if parent_doc is not None:
                    parent_definition = parent_doc.getComponentDefinition(parent_identity)

                    for comp in parent_definition.components:
                        parent_sub_IDs.add(comp.displayId)
                    for seq_anno in parent_definition.sequenceAnnotations:
                        parent_sub_IDs.add(seq_anno.displayId)

            if len(sub_IDs) == len(parent_sub_IDs) and len(sub_IDs) == len(sub_IDs.intersection(parent_sub_IDs)):
                annotated_doc.componentDefinitions.remove(annotated_feature.identity)

                logging.debug('Removed %s from %s', annotated_feature.identity, annotated_doc.name)
            else:
                sub_definitions.update(temp_sub_definitions)

        for annotating_feature in annotating_features:
            if annotating_feature.identity not in sub_definitions:
                annotating_doc = feature_library.get_document(annotating_feature.identity)
                annotating_definition = annotating_doc.getComponentDefinition(annotating_feature.identity)

                annotating_doc.componentDefinitions.remove(annotating_feature.identity)
                for seq_identity in annotating_definition.sequences:
                    try:
                        annotating_doc.sequences.remove(seq_identity)
                    except ValueError:
                        pass

                logging.debug('Removed %s from %s', annotating_feature.identity, annotating_doc.name)

        logging.info('Finished cleaning up')

    def prune(self, target_library, cover_offset, min_target_length, ask_user=True, canonical_library=None, delete_flat=False):
        for target in target_library.features:
            if self.__has_min_length(target, min_target_length):
                logging.info('Pruning %s', target.identity)

                target_doc = target_library.get_document(target.identity)

                target_definition = target_doc.getComponentDefinition(target.identity)

                cut_annos = [(sa.locations.getCut().at, sa.locations.getCut().at, sa.identity, sa.displayId, sa.name, sa.component, set(sa.roles), sa.description) for sa in target_definition.sequenceAnnotations if len(sa.locations) == 1 and sa.locations[0].getTypeURI() == SBOL_CUT]
                annos = [(sa.locations.getRange().start, sa.locations.getRange().end, sa.identity, sa.displayId, sa.name, sa.component, set(sa.roles), sa.description) for sa in target_definition.sequenceAnnotations if len(sa.locations) == 1 and sa.locations[0].getTypeURI() == SBOL_RANGE]
                
                annos.extend(cut_annos)

                if len(self.roles) > 0:
                    self.__filter_annotations(annos, target_definition)

                annos.sort()

                grouped_annos = [[]]

                for anno in annos:
                    if len(grouped_annos) > 1 and self.__is_covered(anno, grouped_annos[-2], cover_offset):
                        grouped_annos[-2].append(anno)
                    elif self.__is_covered(anno, grouped_annos[-1], cover_offset):
                        grouped_annos[-1].append(anno)
                    else:
                        grouped_annos.append([anno])

                if delete_flat:
                    for anno_group in grouped_annos:
                        flat_indices = self.__get_flat_annotation_indices(anno_group)

                        self.__remove_annotations(flat_indices, anno_group, target_definition)

                for anno_group in grouped_annos:
                    if len(anno_group) > 1:
                        selected_indices = self.__select_annotations(target_doc, target_definition, anno_group,
                            ask_user, canonical_library, delete_flat)

                        self.__remove_annotations(selected_indices, anno_group, target_definition)

                # for anno_group in grouped_annos:
                #     if len(anno_group) == 2:
                #         if anno_group[0][5] is None and anno_group[1][5] is not None:
                #             self.__merge_annotations(anno_group[0], anno_group[1], target_definition)
                #         elif anno_group[0][5] is not None and anno_group[1][5] is None:
                #             self.__merge_annotations(anno_group[1], anno_group[0], target_definition)
                 
                logging.info('Finished pruning %s', target.identity)

def main(args=None):
    if args is None:
        args = sys.argv[1:]

    parser = argparse.ArgumentParser()
    parser.add_argument('-n', '--namespace')
    parser.add_argument('-t', '--target_files', nargs='+')
    parser.add_argument('-f', '--feature_files', nargs='*', default=[])
    parser.add_argument('-o', '--output_files', nargs='*', default=[])
    parser.add_argument('-l', '--log_file', nargs='?', default='')
    parser.add_argument('-m', '--min_target_length', nargs='?', default=2000)
    parser.add_argument('-M', '--min_feature_length', nargs='?', default=40)
    parser.add_argument('-c', '--cover_offset', nargs='?', default=14)
    parser.add_argument('-r', '--roles', nargs='*', default=[])
    parser.add_argument('-v', '--validate', action='store_true')
    parser.add_argument('-d', '--delete_flat_annotations', action='store_true')
    # parser.add_argument('-s', '--sbh_URL', nargs='?', default=None)
    # parser.add_argument('-u', '--username', nargs='?', default=None)
    # parser.add_argument('-p', '--password', nargs='?', default=None)
    # parser.add_argument('-F', '--feature_URLs', nargs='*', default=[])
    # parser.add_argument('-T', '--target_URLs', nargs='*', default=[])
    # parser.add_argument('-o', '--sbh_output_file', nargs='?', default=None)
    
    args = parser.parse_args(args)

    if len(args.log_file) > 0:
        logging.basicConfig(level=logging.DEBUG,
                        format='%(asctime)s ; %(levelname)s ; %(message)s',
                        datefmt='%m-%d-%y %H:%M',
                        filename=args.log_file,
                        filemode='w')

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    console_formatter = logging.Formatter('%(levelname)s ; %(message)s')
    
    console_handler.setFormatter(console_formatter)
    
    logging.getLogger('').addHandler(console_handler)

    setHomespace(args.namespace)
    Config.setOption('validate', args.validate)
    Config.setOption('sbol_typed_uris', False)

    feature_docs = []
    for feature_file in args.feature_files:
        feature_docs.append(load_sbol(feature_file))
    feature_library = FeatureLibrary(feature_docs)

    feature_annotater = FeatureAnnotater(feature_library, int(args.min_feature_length))

    feature_pruner = FeaturePruner(feature_library, set(args.roles))

    for i in range (0, len(args.target_files)):
        if args.target_files[i].endswith('.xml') or args.target_files[i].endswith('.sbol'):
            target_doc = load_sbol(args.target_files[i])
        elif (args.target_files[i].endswith('.gb')
                or args.target_files[i].endswith('.genbank')
                or args.target_files[i].endswith('.fasta')
                or args.target_files[i].endswith('.faa')
                or args.target_files[i].endswith('.fa')
                or args.target_files[i].endswith('.fas')
                or args.target_files[i].endswith('.fsa')):
            target_doc = load_non_sbol(args.target_files[i])
        else:
            target_doc = None

            logging.error('Extension of target file %s is unrecognized.', args.target_files[i])
        
        if target_doc is not None:
            target_library = FeatureLibrary([target_doc])

            annotated_identities = feature_annotater.annotate(target_library, int(args.min_target_length))

            added_features = target_library.update()

            feature_pruner.prune(target_library, int(args.cover_offset), int(args.min_target_length),
                delete_flat=args.delete_flat_annotations)

            if len(annotated_identities) > 0:
                annotated_features = []
                annotating_features = []

                for added_feature in added_features:
                    if added_feature.identity in annotated_identities:
                        annotated_features.append(added_feature)
                    else:
                        annotating_features.append(added_feature)

                feature_pruner.clean(target_library, annotated_features, annotating_features)
            else:
                logging.warning('Failed to annotate %s, possibly no constructs found with minimum length %s',
                    args.target_files[i], args.min_target_length)

            if i < len(args.output_files):
                output_file = args.output_files[i]
            else:
                (target_file_base, file_extension) = os.path.splitext(args.target_files[i])
                output_file = target_file_base + '_curated.xml'

            if Config.getOption('validate') == True:
                logging.info('Validating and writing %s', output_file)
            else:
                logging.info('Writing %s', output_file)

            target_doc.write(output_file)

    logging.info('Finished curating')

if __name__ == '__main__':
    main()
