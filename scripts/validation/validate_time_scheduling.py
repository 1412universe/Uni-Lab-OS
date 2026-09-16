#!/usr/bin/env python3
"""独立时间排程集成验证：只用记录执行器，不连接或操作设备。

运行：python scripts/validation/validate_time_scheduling.py
不导入测试助手、不修改 tests；使用真实 EdgeScheduler、后台 CP-SAT 和手动时钟。
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import tempfile
import time
from typing import Any
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from unilabos.app.scheduler.dispatch import RecordingDispatcher
from unilabos.app.scheduler.estimation import DurationEstimator
from unilabos.app.scheduler.models import Handle, WorkflowEdge, WorkflowNode, WorkflowSpec
from unilabos.app.scheduler.service import EdgeScheduler
from unilabos.app.scheduler.time_planning import RollingTimePlanner, TimePlanningConfig
from unilabos.app.scheduler.timing_config import load_timing_rules


class ManualClock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value
    def __call__(self) -> float:
        return self.value
    def advance(self, seconds: float) -> None:
        self.value += seconds


@contextmanager
def running_scheduler(*, busy: set[str] | None = None,
                      estimator: DurationEstimator | None = None
                      ) -> Iterator[tuple[EdgeScheduler, RecordingDispatcher, ManualClock]]:
    clock = ManualClock()
    dispatcher = RecordingDispatcher()
    planner = RollingTimePlanner(TimePlanningConfig(solve_seconds=0.3, refresh_seconds=0.05))
    scheduler = EdgeScheduler(dispatcher=dispatcher, estimator=estimator or DurationEstimator(),
                              clock=clock, time_planner=planner, external_busy_keys=busy)
    try:
        yield scheduler, dispatcher, clock
    finally:
        scheduler.close()


def wait_for(scheduler: EdgeScheduler, predicate: Callable[[], bool], label: str,
             timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        scheduler.reschedule()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(label + '\n' + json.dumps(scheduler.time_schedule_snapshot(), default=str, ensure_ascii=False))


def action(node_id: str, device: str, seconds: float = 1.0, **kwargs: Any) -> WorkflowNode:
    return WorkflowNode(id=node_id, device_id=device, action_name='run', action_type='goal',
                        param={'duration_s':seconds}, **kwargs)


def edge(source: str, target: str, **kwargs: Any) -> WorkflowEdge:
    return WorkflowEdge(uuid=f'{source}-{target}', source_node_id=source, target_node_id=target, **kwargs)


def spec(identity: str, nodes: list[WorkflowNode], *, edges: list[WorkflowEdge] | None = None,
         handles: list[Handle] | None = None, priority: float = 1.0) -> WorkflowSpec:
    return WorkflowSpec(workflow_id=identity, nodes=nodes, edges=edges or [], handles=handles or [],
                        priority=priority, submitted_at=1000)


def dispatched_ids(dispatcher: RecordingDispatcher) -> list[str]:
    return [str(row['node_id']) for row in dispatcher.dispatched]


def durations_and_time_origin() -> None:
    with running_scheduler() as (scheduler, dispatcher, clock):
        scheduler.submit_workflow(spec('durations', [action('short','A',2), action('long','B',7)]))
        wait_for(scheduler, lambda: len(dispatcher.dispatched)==2, 'independent actions did not dispatch')
        running = {item['node_id']:item for item in scheduler.timeline()['running']}
        assert running['short']['estimated_s']==2 and running['long']['estimated_s']==7
        assert running['short']['started_at']==clock.value==1000
        plan=scheduler.time_schedule_snapshot()['latest']
        assert plan['validated'] and plan['time_origin']==1000
        assert len({item['job_id'] for item in dispatcher.dispatched})==2


def priority_after_competing_resource_release() -> None:
    busy={'/devices/shared'}
    with running_scheduler(busy=busy) as (scheduler,dispatcher,clock):
        scheduler.submit_workflow(spec('normal',[action('normal-node','shared',4)],priority=1))
        scheduler.submit_workflow(spec('urgent',[action('urgent-node','shared',2)],priority=10))
        assert not dispatcher.dispatched
        busy.clear()
        wait_for(scheduler,lambda: len(dispatcher.dispatched)>=1,'priority task not dispatched')
        assert dispatched_ids(dispatcher)==['urgent-node'], dispatched_ids(dispatcher)
        first=dispatcher.dispatched[0]['job_id']
        clock.advance(2)
        scheduler.on_job_finished(first, True)
        wait_for(scheduler,lambda: len(dispatcher.dispatched)==2,'normal task not released')
        assert dispatched_ids(dispatcher)==['urgent-node','normal-node']


def overdue_preserves_hold_and_other_tasks_run() -> None:
    with running_scheduler() as (scheduler,dispatcher,clock):
        scheduler.submit_workflow(spec('slow',[action('slow-node','shared',1)]))
        wait_for(scheduler,lambda: len(dispatcher.dispatched)==1,'initial action not dispatched')
        clock.advance(4)
        scheduler.submit_workflow(spec('blocked',[action('blocked-node','shared',1)]))
        scheduler.submit_workflow(spec('independent',[action('independent-node','other',1)]))
        wait_for(scheduler,lambda: 'independent-node' in dispatched_ids(dispatcher),'overdue blocked independent task')
        assert 'blocked-node' not in dispatched_ids(dispatcher)
        assert dispatched_ids(dispatcher).count('slow-node')==1
        view=scheduler.time_schedule_snapshot()['input']
        assert any(hold['hold_id'].startswith('unknown:') for hold in view['holds']), view
        assert any(row[0]=='blocked' for row in view['blocked']), view


def unresolved_condition_does_not_preselect_branch() -> None:
    control=WorkflowNode(id='if',executor_kind='condition',param={
        'bindings':{'answer':{'kind':'node_result','node_uuid':'measure'}},
        'branches':[
            {'label':'if','condition':{'var':'answer'},'node_uuids':['yes'],
             'entry_node_uuids':['yes'],'exit_node_uuids':['yes']},
            {'label':'else','condition':None,'node_uuids':['no'],
             'entry_node_uuids':['no'],'exit_node_uuids':['no']}]})
    with running_scheduler() as (scheduler,dispatcher,clock):
        scheduler.submit_workflow(spec('condition',[action('measure','sensor',2),control,
            action('yes','reactor',3),action('no','reactor',7)],
            edges=[edge('measure','if'),edge('if','yes'),edge('if','no')]))
        wait_for(scheduler,lambda:'measure' in dispatched_ids(dispatcher),'measurement not dispatched')
        assert dispatched_ids(dispatcher)==['measure']
        assert 'condition' in scheduler.time_schedule_snapshot()['input']['partial_tasks']
        clock.advance(2)
        scheduler.on_job_finished(dispatcher.dispatched[0]['job_id'],True,ret_value=True)
        wait_for(scheduler,lambda:'yes' in dispatched_ids(dispatcher),'chosen branch not dispatched')
        assert 'no' not in dispatched_ids(dispatcher)
        assert scheduler.workflow_snapshot('condition')['nodes']['no']['state']=='skipped'


def manual_confirmation_stops_only_its_successors() -> None:
    manual=WorkflowNode(id='confirm',device_id='manual-device',action_name='confirm',action_type='goal',
                        node_type='manual_confirm',param={'duration_s':3},
                        manual_confirmation={'timeout_seconds':3600})
    with running_scheduler() as (scheduler,dispatcher,clock):
        scheduler.submit_workflow(spec('human',[manual,action('after-human','device-after')],
                                       edges=[edge('confirm','after-human')]))
        scheduler.submit_workflow(spec('parallel',[action('parallel-node','parallel-device')]))
        wait_for(scheduler,lambda:'parallel-node' in dispatched_ids(dispatcher),'manual waiting blocked independent work')
        assert 'confirm' not in dispatched_ids(dispatcher) and 'after-human' not in dispatched_ids(dispatcher)
        manual_job=scheduler.workflow_snapshot('human')['nodes']['confirm']['job_id']
        assert manual_job
        clock.advance(100)
        scheduler.resolve_manual_confirmation(manual_job,approved=True)
        wait_for(scheduler,lambda:'confirm' in dispatched_ids(dispatcher),'approved manual action not dispatched')
        assert 'after-human' not in dispatched_ids(dispatcher)
        clock.advance(3)
        scheduler.on_job_finished(manual_job,True,ret_value={'confirmed':True})
        wait_for(scheduler,lambda:'after-human' in dispatched_ids(dispatcher),'manual successor not resumed')
        assert not any(item['device_action_key']=='/devices/manual-device/confirm'
                       for item in scheduler._estimator.stats())


def unresolved_dynamic_parameter_waits_for_parent() -> None:
    handles=[Handle(uuid='source',data_source='executor',handle_key='out',data_key='amount'),
             Handle(uuid='target',data_source='handle',handle_key='in',data_key='duration_s')]
    with running_scheduler() as (scheduler,dispatcher,clock):
        scheduler.submit_workflow(spec('dynamic',[action('read-amount','sensor',2),action('consume','pump',99)],
            edges=[edge('read-amount','consume',source_handle_uuid='source',target_handle_uuid='target')],handles=handles))
        wait_for(scheduler,lambda:'read-amount' in dispatched_ids(dispatcher),'parameter source did not run')
        assert 'consume' not in dispatched_ids(dispatcher)
        assert not scheduler.time_schedule_snapshot()['input_error']
        clock.advance(2)
        scheduler.on_job_finished(dispatcher.dispatched[0]['job_id'],True,ret_value={'amount':5})
        wait_for(scheduler,lambda:'consume' in dispatched_ids(dispatcher),'resolved duration did not run')
        row=next(item for item in scheduler.timeline()['running'] if item['node_id']=='consume')
        assert row['estimated_s']==5, row


def json_configuration_changes_only_declared_features() -> None:
    key='/devices/stir/run'
    with tempfile.TemporaryDirectory() as temp:
        path=Path(temp)/'timing.json'
        path.write_text(json.dumps({key:{'version':'count-v1','feature_paths':['cycles','rpm'],
                                        'formula':'constant_speed_cycles'}}))
        estimator=DurationEstimator()
        assert load_timing_rules(estimator,environ={'UNILABOS_TIMING_RULES_FILE':str(path)})==1
        assert estimator.estimate_timing(key,{'cycles':100,'rpm':60,'label':'a'}).point_s==100
        assert estimator.estimate_timing(key,{'cycles':1,'rpm':60,'label':'b'}).point_s==1
        path.write_text('{"key":{},"key":{}}')
        try: load_timing_rules(DurationEstimator(),str(path))
        except ValueError: pass
        else: raise AssertionError('duplicate rule keys accepted')



def durable_samples_commit_once_and_replay() -> None:
    """真实工作流事务与桥：首次结果提交、重复投递、重启只读恢复。"""
    from unilabos.app.scheduler.timing_store import TimingObservationStore
    from unilabos.workflow.store import WorkflowStore
    from unilabos.workflow.task_input import PreparedTaskInput
    from unilabos.workflow.task_scheduler_bridge import TaskSchedulerBridge

    with tempfile.TemporaryDirectory() as temp:
        store = WorkflowStore(Path(temp) / 'workflow.db')
        workflow_id, task_id, node_id, job_id = [str(uuid4()) for _ in range(4)]
        params = {'cycles': 100, 'rpm': 60, 'label': '原始参数'}
        plan = {'version': 1, 'run_mode': 'normal', 'target_node_uuid': None,
                'nodes': [{'uuid': node_id, 'kind': 'device_action', 'device_id': 'stir',
                           'action_name': 'run', 'action_type': 'goal', 'param': params,
                           'param_schema': {'type': 'object', 'properties': {'goal': {
                               'type': 'object', 'additionalProperties': True}}, 'additionalProperties': False}}],
                'handles': [], 'edges': []}
        prepared = PreparedTaskInput({}, {}, plan, [{'uuid': job_id,
            'workflow_node_uuid': node_id, 'topological_index': 0,
            'executor_kind': 'device_action', 'param': params}])
        store.create_workflow(workflow_uuid=workflow_id, name='独立耗时验证',
                              tags=[], description=None, meta_data={})
        task = store.create_task_with_jobs(workflow_uuid=workflow_id, task_uuid=task_id,
            run_mode='normal', target_node_uuid=None, description=None, meta_data={},
            applied_graph={}, plan_builder=lambda _: prepared)
        estimator = DurationEstimator(minimum_samples=1)
        with running_scheduler(estimator=estimator) as (scheduler, dispatcher, clock):
            bridge = TaskSchedulerBridge(store, scheduler=scheduler, time_scheduling_enabled=True)
            try:
                bridge.submit(task)
                wait_for(scheduler, lambda: len(dispatcher.dispatched)==1, '持久任务未派发')
                assert scheduler._inflight[job_id].timing_origin_observed
                clock.advance(7)
                captured = scheduler.capture_timing_observation(job_id)
                assert captured.actual_s == 7 and captured.params == params
                # 恢复推断的起点不得采样。
                scheduler._inflight[job_id].timing_origin_observed = False
                assert scheduler.capture_timing_observation(job_id) is None
                scheduler._inflight[job_id].timing_origin_observed = True
                scheduler.on_job_finished(job_id, True, ret_value={'ok': True})
                assert store.get_job(job_id)['status'] == 'succeeded'
                with store.read() as connection:
                    sample = TimingObservationStore(connection).get(job_id)
                    count = connection.execute('SELECT count(*) FROM scheduler_timing_observations').fetchone()[0]
                assert count == 1 and sample.actual_s == 7 and sample.params == params
                estimate = estimator.estimate_timing(sample.device_action_key, params, device_id='stir')
                assert estimate.samples == 1 and estimate.point_s == 7, estimate
                # 同一结果晚到重投，时间不能变成 507 秒，也不能重复学习。
                clock.advance(500)
                bridge._project_job_result(task_uuid=task_id, job_uuid=job_id,
                    scheduler_state='success', return_info={'ok': True}, error_info=[],
                    manual_confirmation_status=None)
                assert estimator.estimate_timing(sample.device_action_key, params,
                                                device_id='stir').samples == 1
                with store.read() as connection:
                    assert TimingObservationStore(connection).get(job_id).actual_s == 7
            finally:
                bridge.close()
        recovered = DurationEstimator(minimum_samples=1)
        with running_scheduler(estimator=recovered) as (scheduler, _, _):
            bridge = TaskSchedulerBridge(store, scheduler=scheduler, time_scheduling_enabled=True)
            try:
                restored = recovered.estimate_timing(sample.device_action_key, params, device_id='stir')
                assert restored.samples == 1 and restored.point_s == 7, restored
            finally:
                bridge.close()
        store.close()



def empirical_margin_preserves_parameter_formula() -> None:
    estimator = DurationEstimator(minimum_samples=3)
    key = '/devices/stir/run'
    estimator.register_timing_spec(key, {'version': 'formula-v1',
        'feature_paths': ['cycles', 'rpm'], 'calibration_paths': [],
        'formula': 'constant_speed_cycles'})
    for duration in (104, 106, 108):
        estimator.observe(key, duration, {'cycles': 100, 'rpm': 60}, device_id='stir')
    formula = estimator.estimate_timing(key, {'cycles': 200, 'rpm': 60}, device_id='stir')
    assert (formula.point_s, formula.planned_s, formula.margin_s) == (206, 208, 2)
    assert formula.confidence == 'low' and formula.hard_bound_s is None
    declared = DurationEstimator(minimum_samples=3)
    for duration in (10, 11, 14):
        declared.observe(key, duration, {'duration_s': 10}, device_id='stir')
    estimate = declared.estimate_timing(key, {'duration_s': 10}, device_id='stir')
    assert (estimate.point_s, estimate.planned_s, estimate.margin_s) == (10, 14, 4)
    assert estimate.calibration_samples == 0 and estimate.confidence == 'low'


def main() -> None:
    cases=[durations_and_time_origin,priority_after_competing_resource_release,
           overdue_preserves_hold_and_other_tasks_run,unresolved_condition_does_not_preselect_branch,
           manual_confirmation_stops_only_its_successors,unresolved_dynamic_parameter_waits_for_parent,
           json_configuration_changes_only_declared_features, durable_samples_commit_once_and_replay,
           empirical_margin_preserves_parameter_formula]
    report=[]
    for case in cases:
        started=time.monotonic()
        case()
        report.append({'case':case.__name__,'status':'passed','seconds':round(time.monotonic()-started,3)})
    print(json.dumps({'status':'passed','cases':report},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
