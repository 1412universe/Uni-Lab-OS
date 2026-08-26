"""AIW-03 Local Backend adapter for the durable production Edge protocol."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from unilabos.app.edge_control.local_authority import (
    LocalEdgeAuthorityStore,
    LocalEdgeControlAuthority,
    create_local_edge_control_router,
)
from unilabos.app.scheduler.dispatch import DispatchPayload


def _payload(*, device_id: str = "robot-01") -> DispatchPayload:
    return DispatchPayload(
        job_id=str(uuid.uuid4()),
        task_id=str(uuid.uuid4()),
        node_id=str(uuid.uuid4()),
        workflow_id=str(uuid.uuid4()),
        device_id=device_id,
        action="transfer",
        action_type="normal",
        action_args={"source": "A", "target": "B"},
    )


def _authority(path: Path) -> LocalEdgeControlAuthority:
    return LocalEdgeControlAuthority(
        LocalEdgeAuthorityStore(path), api_key="managed-local-secret"
    )


def test_latest_registration_returns_detached_edge_capabilities(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path / "authority.db")
    instance_uuid = str(uuid.uuid4())
    material_uuid = str(uuid.uuid4())
    try:
        registered = authority.store.register_session(
            {
                "edge_key": "workspace-edge",
                "instance_uuid": instance_uuid,
                "devices": [
                    {
                        "local_id": "robot-01",
                        "name": "Robot",
                        "material_uuid": material_uuid,
                        "actions": [{"name": "transfer", "type": "command"}],
                    }
                ],
            }
        )
        authority.store.set_session_connected(registered["session_uuid"], True)

        snapshot = authority.store.latest_registration()

        assert snapshot is not None
        assert snapshot["edge_uuid"] == registered["edge_uuid"]
        assert snapshot["instance_uuid"] == instance_uuid
        assert snapshot["connected"] is True
        assert snapshot["devices"] == [
            {
                "local_id": "robot-01",
                "name": "Robot",
                "material_uuid": material_uuid,
                "actions": [{"name": "transfer", "type": "command"}],
            }
        ]
        snapshot["devices"][0]["name"] = "mutated"
        assert authority.store.latest_registration()["devices"][0]["name"] == "Robot"  # type: ignore[index]
    finally:
        authority.stop()


def test_dispatch_is_idempotent_and_rejects_changed_identity(tmp_path: Path) -> None:
    authority = _authority(tmp_path / "authority.db")
    payload = _payload()
    try:
        authority.dispatch(payload)
        authority.dispatch(payload)
        assert len(authority.store.pending_commands()) == 1

        changed = DispatchPayload(payload)
        changed["device_id"] = "robot-02"
        with pytest.raises(ValueError, match="identity changed"):
            authority.dispatch(changed)
    finally:
        authority.stop()


def test_http_websocket_round_trip_projects_one_terminal_outcome(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path / "authority.db")
    finished: list[tuple[str, bool, object, str]] = []
    authority.add_job_finished_listener(
        lambda job_id, success, result, suc_type: finished.append(
            (job_id, success, result, suc_type)
        )
    )
    application = FastAPI()
    application.include_router(create_local_edge_control_router(authority))
    client = TestClient(application)
    authorization = {"Authorization": "Bearer managed-local-secret"}
    registration = client.post(
        "/api/v1/edge/sessions",
        headers=authorization,
        json={
            "edge_key": "workspace-edge",
            "instance_uuid": str(uuid.uuid4()),
            "capability_revision": "unilabos-edge-v1",
            "devices": [],
        },
    ).json()["data"]
    payload = _payload()
    authority.dispatch(payload)

    try:
        with client.websocket_connect(
            "/api/v1/edge/ws", headers=authorization
        ) as websocket:
            websocket.send_json(
                {
                    "protocol_version": 1,
                    "message_uuid": str(uuid.uuid4()),
                    "sequence": 0,
                    "type": "hello",
                    "sent_at": "2026-08-13T00:00:00.000000Z",
                    "payload": {
                        "edge_uuid": registration["edge_uuid"],
                        "session_uuid": registration["session_uuid"],
                        "last_ack_command_sequence": 0,
                        "running_jobs": [],
                    },
                }
            )
            command = websocket.receive_json()
            assert command["type"] == "job.start"
            command_payload = command["payload"]
            assert command_payload["job_uuid"] == payload["job_id"]
            event_uuid = str(uuid.uuid4())
            websocket.send_json(
                {
                    "protocol_version": 1,
                    "message_uuid": event_uuid,
                    "sequence": 0,
                    "type": "command.ack",
                    "sent_at": "2026-08-13T00:00:00.000000Z",
                    "payload": {"command_uuid": command["message_uuid"]},
                }
            )
            assert websocket.receive_json()["payload"] == {
                "event_uuid": event_uuid
            }

        headers = {
            "X-Command-UUID": command["message_uuid"],
            "X-Job-Token": command_payload["job_access_token"],
        }
        job = client.get(
            f"/api/v1/edge/jobs/{payload['job_id']}",
            params={"task_uuid": payload["task_id"], "node_uuid": payload["node_id"]},
            headers=headers,
        )
        assert job.status_code == 200
        assert job.json()["data"]["param"] == payload["action_args"]

        outcome = {
            "task_uuid": payload["task_id"],
            "node_uuid": payload["node_id"],
            "outcome": "succeeded",
            "return_info": {"return_value": {"moved": True}},
            "error_info": [],
            "unknown_command_ids": [],
        }
        first = client.put(
            f"/api/v1/edge/jobs/{payload['job_id']}/outcome",
            headers=headers,
            json=outcome,
        )
        second = client.put(
            f"/api/v1/edge/jobs/{payload['job_id']}/outcome",
            headers=headers,
            json=outcome,
        )
        assert first.status_code == second.status_code == 200
        assert finished == [
            (payload["job_id"], True, {"moved": True}, "normal")
        ]
    finally:
        authority.stop()


def test_unknown_outcome_locks_device_until_explicit_reconciliation(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path / "authority.db")
    finished: list[tuple[str, bool, object, str]] = []
    authority.add_job_finished_listener(
        lambda job_id, success, result, suc_type: finished.append(
            (job_id, success, result, suc_type)
        )
    )
    payload = _payload()
    authority.dispatch(payload)
    command = authority.store.pending_commands()[0]
    job_token = command["payload"]["job_access_token"]
    unknown_id = f"workflow-node-job:{payload['job_id']}"
    try:
        result = authority.commit_outcome(
            payload["job_id"],
            command_uuid=command["message_uuid"],
            job_token=job_token,
            payload={
                "outcome": "failed",
                "return_info": {},
                "error_info": [{"message": "Edge disconnected"}],
                "unknown_command_ids": [unknown_id],
            },
        )
        assert result["status"] == "unknown"
        assert finished == []
        with pytest.raises(RuntimeError, match="locked by unresolved UNKNOWN"):
            authority.dispatch(_payload())

        resolution = authority.store.create_unknown_resolution(
            payload["job_id"], reason="operator confirmed safe state"
        )
        commands = authority.store.pending_commands()
        assert commands[-1]["message_uuid"] == resolution["command_uuid"]
        assert commands[-1]["type"] == "job.resolve_unknown"
        authority.resolve_unknown_committed(payload["job_id"])
        assert finished == [(payload["job_id"], False, None, "canceled")]
        assert authority.busy_device_action_keys() == set()
    finally:
        authority.stop()


def test_feedback_projection_failure_is_replayed_after_restart(tmp_path: Path) -> None:
    """Edge 已提交的反馈不得因工作流投影失败而丢失。"""

    database = tmp_path / "authority.db"
    authority = _authority(database)
    payload = _payload()
    authority.dispatch(payload)
    command = authority.store.pending_commands()[0]
    sample = {
        "sequence": 1,
        "feedback_type": "progress",
        "data": {"percent": 25},
        "observed_at": "2026-08-26T10:00:00Z",
        "idempotency_key": "feedback-1",
    }

    authority.add_job_feedback_listener(
        lambda _job_uuid, _sample: (_ for _ in ()).throw(RuntimeError("db down"))
    )
    try:
        with pytest.raises(RuntimeError, match="db down"):
            authority.commit_feedback(
                payload["job_id"],
                command_uuid=command["message_uuid"],
                job_token=command["payload"]["job_access_token"],
                payload=sample,
            )
    finally:
        authority.stop()

    recovered = _authority(database)
    projected: list[tuple[str, dict[str, object]]] = []
    try:
        recovered.replay_pending_projections(
            feedback_listener=lambda job_uuid, item: projected.append(
                (job_uuid, item)
            )
        )
        assert projected == [(payload["job_id"], sample)]

        recovered.replay_pending_projections(
            feedback_listener=lambda job_uuid, item: projected.append(
                (job_uuid, item)
            )
        )
        assert projected == [(payload["job_id"], sample)]
    finally:
        recovered.stop()


def test_outcome_projection_failure_is_replayed_after_restart(tmp_path: Path) -> None:
    """Edge 不可变结果在工作流库恢复后必须可重放。"""

    database = tmp_path / "authority.db"
    authority = _authority(database)
    payload = _payload()
    authority.dispatch(payload)
    command = authority.store.pending_commands()[0]
    authority.add_job_finished_listener(
        lambda *_args: (_ for _ in ()).throw(RuntimeError("db down"))
    )
    try:
        with pytest.raises(RuntimeError, match="db down"):
            authority.commit_outcome(
                payload["job_id"],
                command_uuid=command["message_uuid"],
                job_token=command["payload"]["job_access_token"],
                payload={
                    "outcome": "succeeded",
                    "return_info": {"return_value": {"moved": True}},
                    "error_info": [],
                    "unknown_command_ids": [],
                },
            )
    finally:
        authority.stop()

    recovered = _authority(database)
    projected: list[tuple[str, bool, object, str]] = []
    try:
        recovered.replay_pending_projections(
            finished_listener=lambda job_uuid, success, result, suc_type: projected.append(
                (job_uuid, success, result, suc_type)
            )
        )
        assert projected == [
            (payload["job_id"], True, {"moved": True}, "normal")
        ]

        recovered.replay_pending_projections(
            finished_listener=lambda job_uuid, success, result, suc_type: projected.append(
                (job_uuid, success, result, suc_type)
            )
        )
        assert len(projected) == 1
    finally:
        recovered.stop()


def test_disconnect_marks_dispatched_job_unknown_and_hello_can_reconcile(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path / "authority.db")
    payload = _payload()
    authority.dispatch(payload)
    command = authority.store.pending_commands()[0]
    authority.store.acknowledge_command(command["message_uuid"])
    try:
        assert authority.store.mark_disconnected_jobs_unknown() == [
            payload["job_id"]
        ]
        assert authority.store.job(payload["job_id"])["status"] == "unknown"
        assert authority.busy_device_action_keys() == {
            f"/devices/{payload['device_id']}/{payload['action']}"
        }

        authority.store.reconcile_hello(
            {
                "last_ack_command_sequence": command["sequence"],
                "running_jobs": [
                    {
                        "job_uuid": payload["job_id"],
                        "command_uuid": command["message_uuid"],
                        "state": "running",
                    }
                ],
            }
        )
        assert authority.store.job(payload["job_id"])["status"] == "running"
    finally:
        authority.stop()
