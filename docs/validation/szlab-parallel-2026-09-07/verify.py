"""只读核验已采集的 SZLab 双任务并行结果及库存锁台账。"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence', type=Path)
    parser.add_argument('inventory_db', type=Path)
    args = parser.parse_args()
    root = args.evidence

    def read(name: str):
        return json.loads((root / f'{name}.json').read_text())

    def stamp(value: str) -> float:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()

    identities = read('task-ids')
    assert len(identities) == 2 and len(set(identities)) == 2
    before, after = read('inventory-before'), read('inventory-after')
    all_jobs = []
    summaries = []
    for identity in identities:
        task, jobs = read(identity + '-task'), read(identity + '-jobs')
        assert task['status'] == 'succeeded', task.get('error_info')
        assert len(jobs) == 35 and all(job['status'] == 'succeeded' for job in jobs)
        locks = read(identity + '-execution-locks')['data']
        assert not locks['locks'] and locks['active_device_tenancy_count'] == 0
        reservations = [r for r in after['reservations'] if r['workflow_id'] == identity]
        assert reservations and all(r['status'] in ('released', 'consumed') for r in reservations)
        consumptions = read(identity + '-inventory-consumptions')['data']
        assert sorted(c['actual_quantity'] for c in consumptions) == [0.5, 10.0, 10.0]
        assert all(c['status'] == 'consumed' and c['actual_quantity'] == c['planned_quantity'] for c in consumptions)
        returned = {}
        for key, value in task['output'].items():
            if not isinstance(value, dict) or 'uuid' not in value:
                continue
            def location(snapshot):
                return sorted((r['parent_uuid'], r['slot_id']) for r in snapshot['relations'] if r['child_uuid'] == value['uuid'])
            assert location(before) and location(before) == location(after), (key, value)
            returned[key] = location(after)
        assert len(returned) == 5
        assert task['output']['inspection_result'] == 'OK'
        assert task['output']['density'] == 1.0
        all_jobs.append([j for j in jobs if j.get('control_data', {}).get('actual_executor') and j.get('started_at') and j.get('finished_at')])
        summaries.append({'task_uuid': identity, 'jobs_succeeded': len(jobs), 'returned_materials': returned, 'trace_id': task['trace_context']['trace_id']})
    overlaps = []
    for left in all_jobs[0]:
        for right in all_jobs[1]:
            seconds = min(stamp(left['finished_at']), stamp(right['finished_at'])) - max(stamp(left['started_at']), stamp(right['started_at']))
            if seconds <= 0:
                continue
            ld = left['control_data']['actual_executor']['material_uuid']
            rd = right['control_data']['actual_executor']['material_uuid']
            assert ld != rd, ('同一设备动作发生跨任务重叠', left['uuid'], right['uuid'])
            overlaps.append({'a_job': left['uuid'], 'b_job': right['uuid'], 'a_device': left['control_data']['actual_executor']['local_device_id'], 'b_device': right['control_data']['actual_executor']['local_device_id'], 'seconds': seconds})
    assert overlaps, '未观察到跨任务设备动作重叠'
    with sqlite3.connect(args.inventory_db.resolve().as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        claims = [dict(r) for r in conn.execute('SELECT * FROM station_execution_claim WHERE task_uuid IN (?,?)', identities)]
        leases = [dict(r) for r in conn.execute('SELECT l.*, c.task_uuid FROM station_execution_lock_lease l JOIN station_execution_claim c ON l.claim_uuid=c.claim_uuid WHERE c.task_uuid IN (?,?)', identities)]
    assert len(claims) == 50 and all(c['state'] == 'released' and c['released_at'] for c in claims)
    assert leases and all(
        lease['state'] == 'released' and lease['released_at']
        for lease in leases
    )
    (root / 'claim-audit.json').write_text(json.dumps({'claims': claims, 'leases': leases}, ensure_ascii=False, indent=2))
    # 每个规范资源键在不同任务之间必须没有租约时间交集。
    for left in leases:
        for right in leases:
            if left['task_uuid'] == right['task_uuid'] or left['lock_key'] != right['lock_key']:
                continue
            assert min(stamp(left['released_at']), stamp(right['released_at'])) <= max(stamp(left['acquired_at']), stamp(right['acquired_at'])), ('跨任务资源租约重叠', left, right)
    plc = read('plc-after')
    assert plc['running'] and not plc['state']['active_runs']
    baseline = read('plc-before')['state']['sequence']
    phases = Counter(event['phase'] for event in plc['state']['events'] if event['sequence'] > baseline)
    assert phases == {'accepted': 90, 'completed': 90, 'reset': 90}, phases
    report = {'tasks': summaries, 'cross_task_overlaps': overlaps, 'released_claims': len(claims), 'released_leases': len(leases), 'resource_lease_overlap_count': 0, 'plc_sequence_delta': plc['state']['sequence'] - baseline, 'plc_phases': dict(phases)}
    (root / 'verified.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
