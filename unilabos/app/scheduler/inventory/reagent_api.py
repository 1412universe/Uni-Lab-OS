"""OS 本地模式的 Backend 同形试剂（Reagent）HTTP 适配器。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from unilabos.app.scheduler.inventory.backend_response import call
from unilabos.app.scheduler.inventory.reagent_contract import BackendReagentService


class ReagentModel(BaseModel):
    """忽略 Backend DTO 的未知扩展字段，保留向前兼容。"""

    model_config = ConfigDict(extra="ignore")


class ReagentInfoCreateRequest(ReagentModel):
    cas: str = ""
    name: str
    name_en: Optional[str] = None
    aliases: List[str] = Field(default_factory=list)
    molecular_formula: Optional[str] = None
    smiles: Optional[str] = None
    inchi_key: Optional[str] = None
    molecular_weight: Optional[float] = None
    density_g_per_ml: Optional[float] = None
    physical_state: str = "unknown"
    description: Optional[str] = None
    meta_data: Dict[str, Any] = Field(default_factory=dict)


class ReagentInfoUpdateRequest(ReagentModel):
    cas: Optional[str] = None
    name: Optional[str] = None
    name_en: Optional[str] = None
    aliases: Optional[List[str]] = None
    molecular_formula: Optional[str] = None
    smiles: Optional[str] = None
    inchi_key: Optional[str] = None
    molecular_weight: Optional[float] = None
    density_g_per_ml: Optional[float] = None
    physical_state: Optional[str] = None
    description: Optional[str] = None
    meta_data: Optional[Dict[str, Any]] = None


class ReagentCreateRequest(ReagentModel):
    material_uuid: UUID
    reagent_info_uuid: Optional[UUID] = None
    cas: str = ""
    physical_state: str = "unknown"
    density_g_per_ml: Optional[float] = None
    concentration_value: Optional[float] = None
    concentration_unit: Optional[str] = None
    quantity: float
    quantity_unit: str
    source: Optional[str] = None
    observed_at: Optional[datetime] = None
    description: Optional[str] = None
    meta_data: Dict[str, Any] = Field(default_factory=dict)


class ReagentUpdateRequest(ReagentModel):
    concentration_value: Optional[float] = None
    concentration_unit: Optional[str] = None
    quantity: float
    quantity_unit: str
    source: Optional[str] = None
    observed_at: Optional[datetime] = None
    expected_revision: Optional[int] = None
    description: Optional[str] = None
    meta_data: Dict[str, Any] = Field(default_factory=dict)


def create_reagent_router(service: BackendReagentService) -> APIRouter:
    """创建化学身份、试剂实例、CAS 查询和台账路由。

    参数：``service`` 是绑定当前 ``inventory.db`` 的领域服务。返回：挂在
    ``/api/v1`` 父路由下的无前缀 Router。异常：装配错误原样抛出。
    """

    router = APIRouter(tags=["backend-reagent-contract"])

    @router.get("/compounds/{cas}")
    def lookup_compound(cas: str) -> JSONResponse:
        return call(service.lookup_compound, cas)

    @router.post("/reagent-infos")
    def create_reagent_info(body: ReagentInfoCreateRequest) -> JSONResponse:
        return call(
            service.create_reagent_info,
            body.model_dump(mode="json"),
            status_code=201,
        )

    @router.get("/reagent-infos")
    def list_reagent_infos(
        page: int = Query(default=0),
        page_size: int = Query(default=0),
        name: str = Query(default=""),
        cas: str = Query(default=""),
        physical_state: str = Query(default=""),
    ) -> JSONResponse:
        return call(
            service.list_reagent_infos,
            page=page,
            page_size=page_size,
            name=name,
            cas=cas,
            physical_state=physical_state,
        )

    @router.get("/reagent-infos/{reagent_info_uuid}")
    def get_reagent_info(reagent_info_uuid: UUID) -> JSONResponse:
        return call(service.get_reagent_info, str(reagent_info_uuid))

    @router.get("/reagent-infos/{reagent_info_uuid}/structure-3d")
    def get_reagent_info_structure(reagent_info_uuid: UUID) -> JSONResponse:
        return call(service.get_reagent_info_structure, str(reagent_info_uuid))

    @router.put("/reagent-infos/{reagent_info_uuid}")
    def update_reagent_info(
        reagent_info_uuid: UUID, body: ReagentInfoUpdateRequest
    ) -> JSONResponse:
        return call(
            service.update_reagent_info,
            str(reagent_info_uuid),
            body.model_dump(mode="json", exclude_unset=True),
        )

    @router.delete("/reagent-infos/{reagent_info_uuid}")
    def delete_reagent_info(reagent_info_uuid: UUID) -> JSONResponse:
        return call(service.delete_reagent_info, str(reagent_info_uuid))

    @router.post("/reagents")
    def create_reagent(body: ReagentCreateRequest) -> JSONResponse:
        return call(
            service.create_reagent,
            body.model_dump(mode="json"),
            status_code=201,
        )

    @router.get("/reagents")
    def list_reagents(
        page: int = Query(default=0),
        page_size: int = Query(default=0),
        material_uuid: Optional[UUID] = Query(default=None),
        reagent_info_uuid: Optional[UUID] = Query(default=None),
        keyword: str = Query(default=""),
        cas: str = Query(default=""),
        barcode: str = Query(default=""),
    ) -> JSONResponse:
        return call(
            service.list_reagents,
            page=page,
            page_size=page_size,
            material_uuid=str(material_uuid) if material_uuid else "",
            reagent_info_uuid=(
                str(reagent_info_uuid) if reagent_info_uuid else ""
            ),
            keyword=keyword,
            cas=cas,
            barcode=barcode,
        )

    @router.get("/reagents/{reagent_uuid}")
    def get_reagent(reagent_uuid: UUID) -> JSONResponse:
        return call(service.get_reagent, str(reagent_uuid))

    @router.put("/reagents/{reagent_uuid}")
    def update_reagent(
        reagent_uuid: UUID, body: ReagentUpdateRequest
    ) -> JSONResponse:
        return call(
            service.update_reagent,
            str(reagent_uuid),
            body.model_dump(mode="json"),
        )

    @router.delete("/reagents/{reagent_uuid}")
    def delete_reagent(reagent_uuid: UUID) -> JSONResponse:
        return call(service.delete_reagent, str(reagent_uuid))

    @router.get("/reagent-history/{history_uuid}")
    def get_reagent_history(history_uuid: UUID) -> JSONResponse:
        return call(service.get_reagent_history, str(history_uuid))

    @router.get("/materials/{material_uuid}/reagent-history")
    def list_reagent_history(
        material_uuid: UUID,
        page: int = Query(default=0),
        page_size: int = Query(default=0),
    ) -> JSONResponse:
        return call(
            service.list_reagent_history,
            str(material_uuid),
            page=page,
            page_size=page_size,
        )

    return router


__all__ = ["create_reagent_router"]
