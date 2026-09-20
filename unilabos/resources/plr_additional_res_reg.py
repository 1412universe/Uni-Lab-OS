

def register():
    # GN 9320 台面在 workstation.GN 路径；优先注册，避免 find_subclass 命中旧 9300 Deck。
    try:
        from unilabos.devices.workstation.GN.liquid_handling.prcxi.prcxi import (  # noqa: F401
            PRCXI9300Deck,
            PRCXI9300Plate,
            PRCXI9300PlateAdapter,
            PRCXI9300TipRack,
            PRCXI9300Trash,
            PRCXI9300TubeRack,
        )
    except ImportError:  # 无 GN 树时回退内置 9300
        # noinspection PyUnresolvedReferences
        from unilabos.devices.liquid_handling.prcxi.prcxi import PRCXI9300Deck  # noqa: F401
        # noinspection PyUnresolvedReferences
        from unilabos.devices.liquid_handling.prcxi.prcxi import PRCXI9300Plate  # noqa: F401
        from unilabos.devices.liquid_handling.prcxi.prcxi import PRCXI9300PlateAdapter  # noqa: F401
        from unilabos.devices.liquid_handling.prcxi.prcxi import PRCXI9300TipRack  # noqa: F401
        from unilabos.devices.liquid_handling.prcxi.prcxi import PRCXI9300Trash  # noqa: F401
        from unilabos.devices.liquid_handling.prcxi.prcxi import PRCXI9300TubeRack  # noqa: F401
    try:
        from gn_lab.resources.decks import PRCXIDeck  # noqa: F401
    except ImportError:
        pass
    # noinspection PyUnresolvedReferences
    from unilabos.devices.workstation.workstation_base import WorkStationContainer

    from unilabos.devices.liquid_handling.laiyu.laiyu import TransformXYZDeck
    from unilabos.devices.liquid_handling.laiyu.laiyu import TransformXYZContainer

    from unilabos.devices.liquid_handling.rviz_backend import UniLiquidHandlerRvizBackend
    from unilabos.devices.liquid_handling.laiyu.backend.laiyu_v_backend import UniLiquidHandlerLaiyuBackend

