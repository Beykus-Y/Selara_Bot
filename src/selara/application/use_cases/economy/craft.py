from __future__ import annotations

from selara.application.economy_interfaces import EconomyRepository
from selara.application.use_cases.economy.catalog import RECIPES
from selara.application.use_cases.economy.common import get_account_or_error, resolve_scope_or_error
from selara.application.use_cases.economy.results import CraftResult


def normalize_recipe_input(value: str) -> str:
    normalized = " ".join((value or "").strip().lower().split())
    aliases = {
        "пицца": "pizza",
        "салат": "salad",
        "овощной салат": "salad",
        "чипсы": "chips",
        "кукурузные чипсы": "chips",
    }
    return aliases.get(normalized, normalized)


async def execute(
    repo: EconomyRepository,
    *,
    economy_mode: str,
    chat_id: int | None,
    user_id: int,
    recipe_code: str,
) -> CraftResult:
    normalized_recipe = normalize_recipe_input(recipe_code)
    recipe = RECIPES.get(normalized_recipe)
    if recipe is None:
        return CraftResult(
            accepted=False,
            reason="Рецепт не найден. Откройте /craft без аргументов.",
            recipe_code=None,
            crafted_item_code=None,
            crafted_quantity=0,
        )

    scope, error = await resolve_scope_or_error(repo, economy_mode=economy_mode, chat_id=chat_id, user_id=user_id)
    if scope is None:
        return CraftResult(
            accepted=False,
            reason=error or "Не удалось определить режим экономики.",
            recipe_code=recipe.code,
            crafted_item_code=None,
            crafted_quantity=0,
        )

    account, _ = await get_account_or_error(repo, scope=scope, user_id=user_id)

    for item_code, quantity in recipe.ingredients:
        item = await repo.get_inventory_item(account_id=account.id, item_code=item_code)
        if item is None or item.quantity < quantity:
            return CraftResult(
                accepted=False,
                reason="Недостаточно ингредиентов для рецепта.",
                recipe_code=recipe.code,
                crafted_item_code=None,
                crafted_quantity=0,
            )

    for item_code, quantity in recipe.ingredients:
        await repo.add_inventory_item(account_id=account.id, item_code=item_code, delta=-quantity)

    await repo.add_inventory_item(
        account_id=account.id,
        item_code=recipe.result_item_code,
        delta=recipe.result_quantity,
    )

    return CraftResult(
        accepted=True,
        reason=None,
        recipe_code=recipe.code,
        crafted_item_code=recipe.result_item_code,
        crafted_quantity=recipe.result_quantity,
    )
