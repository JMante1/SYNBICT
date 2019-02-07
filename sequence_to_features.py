# SBOL Condenser

from sbol import *
from Bio.Seq import Seq

def extract_sequence(sub_def, seq_anno, comp_def, doc):
    if len(comp_def.sequences) == 1 and len(seq_anno.locations) == 1:
        comp_seq = doc.sequences.get(comp_def.sequences[0])

        rang = seq_anno.locations.getRange()
        if rang.start > len(comp_seq.elements):
            return False
        else:
            elements = comp_seq.elements[rang.start - 1:rang.end]

        if rang.orientation == 'http://sbols.org/v2#reverseComplement':
            elements = str(Seq(elements).reverse_complement())

        sub_seq = Sequence(sub_def.displayId + '_seq', elements, SBOL_ENCODING_IUPAC, '1')
        
        doc.addSequence(sub_seq)

        sub_def.sequences = sub_def.sequences + [sub_seq.identity]
        
        return True
    else:
        return False
    
def reconcile_roles(canonical_def, comp_def, generic_roles):
    same_roles = True

    for role in comp_def.roles:
        if role not in canonical_def.roles:
            same_roles = False

    if same_roles:
        for role in canonical_def.roles:
            if role not in comp_def.roles:
                same_roles = False

    if not same_roles:
        canonical_roles = []

        for role in comp_def.roles:
            if role not in generic_roles:
                canonical_roles.append(role)

        if len(canonical_roles) > 0:
            canonical_def.roles = canonical_roles

setHomespace('http://hub.sd2e.org/')
Config.setOption('sbol_typed_uris', False)
Config.setOption('validate', True)
# Config.setOption('serialization_format', 'rdfxml')
Config.setOption('version', '1')

doc = Document()

part_shop = PartShop('https://hub.sd2e.org/')
part_shop.login('sd2_service@sd2e.org', 'jWJ1yztJl2f7RaePHMtXmxBBHwNt')

# part_shop.pull('https://hub.sd2e.org/user/sd2e/design/yeast_gates_plasmids/1', doc)
for i in range(1, 10):
    part_shop.pull('https://hub.sd2e.org/user/sd2e/design/YG_plasmid_00' + str(i) + '/1', doc)
    print('load ' + str(i))
for i in range(10, 26):
    part_shop.pull('https://hub.sd2e.org/user/sd2e/design/YG_plasmid_0' + str(i) + '/1', doc)
    print('load ' + str(i))
    
setHomespace('http://hub.sd2e.org/user/sd2e/design')

non_canonical_ids = set()
non_canonical_names = set()
name_to_def = {}
generic_roles = {
    SO_GENE, 
    'http://identifiers.org/so/SO:0000001', 
    'http://identifiers.org/so/SO:0000110', 
    'http://identifiers.org/so/SO:0000804'
}

for comp_def in doc.componentDefinitions:
    for seq_anno in comp_def.sequenceAnnotations:
        if seq_anno.component is None:
            print('SequenceAnnotation ' + seq_anno.identity + ' has no associated Component.')
        else:
            comp = comp_def.components.get(seq_anno.component)
            sub_def = doc.componentDefinitions.get(comp.definition)
            
            if len(sub_def.sequences) > 0 or extract_sequence(sub_def, seq_anno, comp_def, doc):
                sub_seq = doc.sequences.get(sub_def.sequences[0])
                
                if len(sub_seq.elements) > 1:
                    if sub_def.name in name_to_def:
                        canonical_def = name_to_def[sub_def.name]
                        canonical_seq = doc.sequences.get(canonical_def.sequences[0])

                        if canonical_seq.elements.lower() == sub_seq.elements.lower():
                            reconcile_roles(canonical_def, sub_def, generic_roles)
                        else:
                            non_canonical_ids.add(sub_def.identity)
                            non_canonical_names.add(sub_def.name)
                    else:
                        name_to_def[sub_def.name] = sub_def
            else:
                non_canonical_ids.add(sub_def.identity)
                non_canonical_names.add(sub_def.name)
                
for comp_def in doc.componentDefinitions:  
    deleted_comp_ids = set()
    
    for comp in comp_def.components:
        if comp.definition not in non_canonical_ids:
            sub_def = doc.componentDefinitions.get(comp.definition)
            
            if sub_def.name in name_to_def:
                canonical_def = name_to_def[sub_def.name]
                
                if sub_def.identity != canonical_def.identity:
                    comp.definition = canonical_def.identity
                    
                    for sub_seq_id in sub_def.sequences:
                        doc.sequences.remove(sub_seq_id)

                    doc.componentDefinitions.remove(sub_def.identity)
            else:
                deleted_comp_ids.add(comp.identity)
                
                for sub_seq_id in sub_def.sequences:
                    doc.sequences.remove(sub_seq_id)

                doc.componentDefinitions.remove(sub_def.identity)
            
    for deleted_comp_id in deleted_comp_ids:
        comp_def.components.remove(deleted_comp_id)
        
    for anno in comp_def.sequenceAnnotations:
        if anno.component in deleted_comp_ids:
            anno.component = None
          
print('writing')
doc.write('bograth.xml')
print(non_canonical_names)