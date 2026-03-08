from typing import Literal

AliasMode = Literal["aliases_if_exists", "both", "standard_only"]

ALIAS_MODE_DEFAULT: AliasMode = "both"
ALIAS_MODE_VALUES: tuple[AliasMode, ...] = ("aliases_if_exists", "both", "standard_only")

TEXT_ALIAS_MAX_LEN = 64
