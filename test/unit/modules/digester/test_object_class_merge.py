# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from src.documents.normalize import canonical_object_class_key
from src.modules.digester.aggregation.merges import merge_object_classes
from src.modules.digester.schemas import ExtendedObjectClass


def test_canonical_key_collapses_whitespace_variants():
    assert canonical_object_class_key("Service Account") == canonical_object_class_key("ServiceAccount")
    assert canonical_object_class_key("  Access   Role ") == "accessrole"


def test_merge_preserves_metadata_when_rich_variant_seen_first():
    """Whitespace-variant duplicates must merge without losing structural metadata.

    Regression: merging keyed on the spaced name and then popping the spaced
    variant discarded the entry that had accumulated superclass/abstract/
    description, keeping the sparse no-space variant.
    """
    classes = [
        ExtendedObjectClass(
            name="Service Account",
            description="A rich description",
            superclass="Account",
            abstract=True,
            embedded=True,
        ),
        ExtendedObjectClass(name="ServiceAccount", description=""),
    ]

    merged = merge_object_classes(classes)

    assert len(merged) == 1
    assert merged[0].superclass == "Account"
    assert merged[0].abstract is True
    assert merged[0].embedded is True
    assert merged[0].description == "A rich description"


def test_merge_preserves_metadata_when_sparse_variant_seen_first():
    """Metadata must survive regardless of which variant is encountered first."""
    classes = [
        ExtendedObjectClass(name="ServiceAccount", description=""),
        ExtendedObjectClass(
            name="Service Account",
            description="Rich",
            superclass="Account",
            abstract=True,
        ),
    ]

    merged = merge_object_classes(classes)

    assert len(merged) == 1
    assert merged[0].superclass == "Account"
    assert merged[0].abstract is True
    assert merged[0].description == "Rich"
