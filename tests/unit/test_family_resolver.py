from selara.domain.entities import FamilyBundle
from selara.presentation.family_resolver import resolve_family_relations

def test_resolve_family_relations_deduplicates_by_priority() -> None:
    # Ego: 1
    # Parents: [2, 3]
    # Step-parents: [3, 4] -> 3 is duplicate (removed, 4 remains)
    # Spouse: 5
    # Siblings: [5, 6] -> 5 is duplicate (removed, 6 remains)
    # Children: [6, 7] -> 6 is duplicate (removed, 7 remains)
    # Pets: [7, 8] -> 7 is duplicate (removed, 8 remains)
    # Grandparents: [8, 9] -> 8 is duplicate (removed, 9 remains)
    # Owners: [9, 10] -> 9 is duplicate (removed, 10 remains)
    
    bundle = FamilyBundle(
        subject_user_id=1,
        parents=(2, 3),
        step_parents=(3, 4),
        spouse_user_id=5,
        siblings=(5, 6),
        children=(6, 7),
        pets=(7, 8),
        grandparents=(8, 9),
        owners=(9, 10)
    )
    
    resolved = resolve_family_relations(bundle)
    
    assert resolved.subject_user_id == 1
    assert resolved.parents == (2, 3)
    assert resolved.step_parents == (4,)
    assert resolved.spouse_user_id == 5
    assert resolved.siblings == (6,)
    assert resolved.children == (7,)
    assert resolved.pets == (8,)
    assert resolved.grandparents == (9,)
    assert resolved.owners == (10,)


def test_resolve_family_relations_excludes_ego() -> None:
    # Ego: 1
    # Spouse: 1 -> duplicate (removed)
    # Parents: (1, 2) -> 1 duplicate (removed)
    # Siblings: (1, 3) -> 1 duplicate (removed)
    # Children: (1, 4) -> 1 duplicate (removed)
    
    bundle = FamilyBundle(
        subject_user_id=1,
        spouse_user_id=1,
        parents=(1, 2),
        grandparents=(),
        step_parents=(),
        siblings=(1, 3),
        children=(1, 4),
        pets=(),
        owners=()
    )
    
    resolved = resolve_family_relations(bundle)
    
    assert resolved.subject_user_id == 1
    assert resolved.spouse_user_id is None
    assert resolved.parents == (2,)
    assert resolved.siblings == (3,)
    assert resolved.children == (4,)
