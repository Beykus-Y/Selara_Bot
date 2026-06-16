from __future__ import annotations

import logging
from selara.domain.entities import FamilyBundle

logger = logging.getLogger(__name__)

def resolve_family_relations(bundle: FamilyBundle) -> FamilyBundle:
    """
    Deduplicates family relations in a FamilyBundle based on priority:
    Ego (subject) > Parents > Step-parents > Spouse > Siblings > Children > Pets > Grandparents > Owners.
    
    Each user ID will belong to exactly one bucket corresponding to the highest priority relation.
    """
    seen: set[int] = set()
    
    # 1. Ego (center of dynasty)
    ego = bundle.subject_user_id
    seen.add(ego)
    
    # 2. Parents
    parents: list[int] = []
    for p in (bundle.parents or ()):
        if p not in seen:
            parents.append(p)
            seen.add(p)
        else:
            logger.warning("User %d deduplicated from parents (already seen).", p)
            
    # 3. Step parents
    step_parents: list[int] = []
    for sp in (bundle.step_parents or ()):
        if sp not in seen:
            step_parents.append(sp)
            seen.add(sp)
        else:
            logger.warning("User %d deduplicated from step-parents (already seen).", sp)
            
    # 4. Spouse / Partner
    spouse = None
    if bundle.spouse_user_id is not None:
        if bundle.spouse_user_id not in seen:
            spouse = bundle.spouse_user_id
            seen.add(spouse)
        else:
            logger.warning("User %d deduplicated from spouse (already seen).", bundle.spouse_user_id)
            
    # 5. Siblings
    siblings: list[int] = []
    for sib in (bundle.siblings or ()):
        if sib not in seen:
            siblings.append(sib)
            seen.add(sib)
        else:
            logger.warning("User %d deduplicated from siblings (already seen).", sib)
            
    # 6. Children
    children: list[int] = []
    for ch in (bundle.children or ()):
        if ch not in seen:
            children.append(ch)
            seen.add(ch)
        else:
            logger.warning("User %d deduplicated from children (already seen).", ch)
            
    # 7. Pets
    pets: list[int] = []
    for pet in (bundle.pets or ()):
        if pet not in seen:
            pets.append(pet)
            seen.add(pet)
        else:
            logger.warning("User %d deduplicated from pets (already seen).", pet)
            
    # 8. Grandparents (Ancestors)
    grandparents: list[int] = []
    for gp in (bundle.grandparents or ()):
        if gp not in seen:
            grandparents.append(gp)
            seen.add(gp)
        else:
            logger.warning("User %d deduplicated from grandparents (already seen).", gp)
            
    # 9. Owners
    owners: list[int] = []
    for o in (bundle.owners or ()):
        if o not in seen:
            owners.append(o)
            seen.add(o)
        else:
            logger.warning("User %d deduplicated from owners (already seen).", o)
            
    return FamilyBundle(
        subject_user_id=ego,
        spouse_user_id=spouse,
        parents=tuple(parents),
        grandparents=tuple(grandparents),
        step_parents=tuple(step_parents),
        siblings=tuple(siblings),
        children=tuple(children),
        pets=tuple(pets),
        owners=tuple(owners)
    )
