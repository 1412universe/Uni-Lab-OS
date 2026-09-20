"""GN 资源注册。"""

from gn_lab.resources.decks import PRCXIDeck, PRCXI_DECK_SITES
from gn_lab.resources.labware import (
    prcxi_1250ul_tips,
    prcxi_96_deepwell,
    prcxi_nest_1_troughplate,
    prcxi_trash,
)

__all__ = [
    "PRCXIDeck",
    "PRCXI_DECK_SITES",
    "prcxi_nest_1_troughplate",
    "prcxi_96_deepwell",
    "prcxi_1250ul_tips",
    "prcxi_trash",
]
