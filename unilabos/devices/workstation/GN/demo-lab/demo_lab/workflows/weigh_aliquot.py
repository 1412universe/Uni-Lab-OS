from typing import Annotated, Literal, TypedDict

from pydantic import Field
from demo_lab.devices.stations import BalanceStation, CapStation, PipetteStation
from demo_lab.devices.transport_robot import TransportRobot
from demo_lab.resources.materials import aliquot_vial, stock_vial, tip_box
from unilabos.registry.placeholder_type import ResourceSlot
from unilabos.workflow.authoring import MaterialCustodyPolicy, MaterialFlowRole, device, material_source, resource_ref, workflow


class WeighAliquotResult(TypedDict):
    product_vial: ResourceSlot
    used_stock_vial: ResourceSlot
    measured_mass_g: float
    commanded_volume_ul: int
    message: str


transport_robot: TransportRobot = device("transport_robot_01")
balance_station: BalanceStation = device("balance_station_01")
pipette_station: PipetteStation = device("pipette_station_01")
cap_station: CapStation = device("cap_station_01")


@workflow(
    workflow_uuid="7e2a9c14-6b5d-4f81-a3c0-91d8e4b2f6a5",
    displayname="标准样品称量分装",
    description="取标准样品母液称量后，按目标体积分装到空瓶，封盖后回库，母液放到已用区。",
)
def weigh_aliquot(
    *,
    sample_id: str = "ASSAY-2026-001",
    target_aliquot_volume_ul: Annotated[int, Field(title="分装体积", description="单位 μL", ge=1, le=5000)] = 200,
    source_stock_site: Literal["S01"] = "S01",
    source_empty_site: Literal["E01"] = "E01",
    tip_box_site: Literal["T01"] = "T01",
    used_stock_target_site: Literal["U01"] = "U01",
    product_vial_target_site: Literal["F01"] = "F01",
) -> WeighAliquotResult:
    # unilab:node_uuid=2a6f1c80-0d4b-4e91-9c3a-7b18e5d4a201
    source_stock = material_source(resource_template=stock_vial, mode="existing", mount=resource_ref("source_rack"), material_uuid=None, site=source_stock_site, slot_range=None, flow_role=MaterialFlowRole.PRIMARY_SAMPLE, custody_policy=MaterialCustodyPolicy.TASK_EXCLUSIVE)
    # unilab:node_uuid=2a6f1c80-0d4b-4e91-9c3a-7b18e5d4a202
    source_empty = material_source(resource_template=aliquot_vial, mode="existing", mount=resource_ref("source_rack"), material_uuid=None, site=source_empty_site, slot_range=None, flow_role=MaterialFlowRole.CONSUMABLE, custody_policy=MaterialCustodyPolicy.TASK_EXCLUSIVE)
    # unilab:node_uuid=2a6f1c80-0d4b-4e91-9c3a-7b18e5d4a203
    source_tip_box = material_source(resource_template=tip_box, mode="existing", mount=resource_ref("tip_warehouse"), material_uuid=None, site=tip_box_site, slot_range=None, flow_role=MaterialFlowRole.CONSUMABLE, custody_policy=MaterialCustodyPolicy.SHARED_SOURCE)
    # unilab:node_uuid=2a6f1c80-0d4b-4e91-9c3a-7b18e5d4a204
    stock_at_balance = transport_robot.transfer_material_atomic(resource=source_stock, source_warehouse=resource_ref("source_rack"), target_warehouse=resource_ref("balance_process_rack"), target_device="balance_station_01", source_site=source_stock_site, target_site="BAL1", check_source_presence=True, check_target_presence=True, check_gripper_payload=True)
    # unilab:node_uuid=2a6f1c80-0d4b-4e91-9c3a-7b18e5d4a205
    weighed = balance_station.weigh(stock_vial=stock_at_balance.resource)
    # unilab:node_uuid=2a6f1c80-0d4b-4e91-9c3a-7b18e5d4a206
    stock_at_pipette = transport_robot.transfer_material_atomic(resource=weighed.stock_vial, source_warehouse=resource_ref("balance_process_rack"), target_warehouse=resource_ref("pipette_process_rack"), target_device="pipette_station_01", source_site="BAL1", target_site="PIP_S", check_source_presence=True, check_target_presence=True, check_gripper_payload=True)
    # unilab:node_uuid=2a6f1c80-0d4b-4e91-9c3a-7b18e5d4a207
    empty_at_pipette = transport_robot.transfer_material_atomic(resource=source_empty, source_warehouse=resource_ref("source_rack"), target_warehouse=resource_ref("pipette_process_rack"), target_device="pipette_station_01", source_site=source_empty_site, target_site="PIP_T", check_source_presence=True, check_target_presence=True, check_gripper_payload=True)
    # unilab:node_uuid=2a6f1c80-0d4b-4e91-9c3a-7b18e5d4a208
    aliquoted = pipette_station.aliquot(stock_vial=stock_at_pipette.resource, aliquot_vial=empty_at_pipette.resource, tip_box=source_tip_box, target_aliquot_volume_ul=target_aliquot_volume_ul)
    # unilab:node_uuid=2a6f1c80-0d4b-4e91-9c3a-7b18e5d4a209
    filled_at_cap = transport_robot.transfer_material_atomic(resource=aliquoted.aliquot_vial, source_warehouse=resource_ref("pipette_process_rack"), target_warehouse=resource_ref("cap_process_rack"), target_device="cap_station_01", source_site="PIP_T", target_site="CAP1", check_source_presence=True, check_target_presence=True, check_gripper_payload=True)
    # unilab:node_uuid=2a6f1c80-0d4b-4e91-9c3a-7b18e5d4a20a
    closed = cap_station.close_cap(aliquot_vial=filled_at_cap.resource, sample_id=sample_id)
    # unilab:node_uuid=2a6f1c80-0d4b-4e91-9c3a-7b18e5d4a20b
    product_vial = transport_robot.transfer_material_atomic(resource=closed.aliquot_vial, source_warehouse=resource_ref("cap_process_rack"), target_warehouse=resource_ref("finished_rack"), target_device="host_node", source_site="CAP1", target_site=product_vial_target_site, check_source_presence=True, check_target_presence=True, check_gripper_payload=True)
    # unilab:node_uuid=2a6f1c80-0d4b-4e91-9c3a-7b18e5d4a20c
    used_stock = transport_robot.transfer_material_atomic(resource=aliquoted.stock_vial, source_warehouse=resource_ref("pipette_process_rack"), target_warehouse=resource_ref("used_rack"), target_device="host_node", source_site="PIP_S", target_site=used_stock_target_site, check_source_presence=True, check_target_presence=True, check_gripper_payload=True)
    return {"product_vial": product_vial.resource, "used_stock_vial": used_stock.resource, "measured_mass_g": weighed.measured_mass_g, "commanded_volume_ul": aliquoted.commanded_volume_ul, "message": closed.message}
