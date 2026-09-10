import argparse
import collections
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

parser = argparse.ArgumentParser()
parser.add_argument('task_id')
parser.add_argument('output_dir')
args = parser.parse_args()
out = Path(args.output_dir)
out.mkdir(parents=True, exist_ok=True)
base = 'http://127.0.0.1:51803/api/v1'

def get(path):
    with urlopen(path if path.startswith('http') else base + path, timeout=30) as response:
        return json.load(response)

def save(name, data):
    (out / (name + '.json')).write_text(json.dumps(data, ensure_ascii=False, indent=2))

prefix = '/workflow-tasks/' + args.task_id
previous = None
for _ in range(180):
    task = get(prefix)['data']
    jobs = get(prefix + '/jobs')['data']
    state = (task['status'], dict(collections.Counter(j['status'] for j in jobs)))
    if state != previous:
        print(datetime.now(timezone.utc).isoformat(), state, flush=True)
        previous = state
    if task['status'] in ('succeeded', 'failed', 'canceled', 'timeout'):
        break
    time.sleep(5)
save('task', task)
save('jobs', jobs)
assert task['status'] == 'succeeded', task.get('error_info')
assert len(jobs) == 35 and all(j['status'] == 'succeeded' for j in jobs)
locks = get(prefix + '/execution-locks')['data']
save('locks', locks)
assert not locks['locks'] and locks['active_device_tenancy_count'] == 0
inventory = get('/inventory/snapshot')
save('inventory', inventory)
reservations = [r for r in inventory['reservations'] if r['workflow_id'] == args.task_id]
assert reservations and all(r['status'] in ('released', 'consumed') for r in reservations)
before = json.loads((Path(__file__).parent / 'inventory-before.json').read_text())
locations = {}
for key, value in task['output'].items():
    if not isinstance(value, dict) or 'uuid' not in value:
        continue
    def location(snapshot):
        return sorted((r['parent_uuid'], r['slot_id']) for r in snapshot['relations'] if r['child_uuid'] == value['uuid'])
    assert location(before) == location(inventory), (key, location(inventory))
    locations[key] = location(inventory)
consumptions = get(prefix + '/inventory-consumptions')['data']
save('consumptions', consumptions)
assert len(consumptions) == 3
assert sorted(c['actual_quantity'] for c in consumptions) == [0.5, 10.0, 10.0]
assert all(c['status'] == 'consumed' and c['actual_quantity'] == c['planned_quantity'] for c in consumptions)
events = get(prefix + '/events?limit=500')['data']
save('events', events)
plc = get('http://127.0.0.1:18766/api/agent/szlab/state')
save('plc', plc)
assert plc['running'] and not plc['state']['active_runs']
assert task['output']['inspection_result'] == 'OK'
assert task['output']['density'] == 1.0
summary = {
    'task_uuid': args.task_id, 'status': task['status'], 'jobs_succeeded': len(jobs),
    'trace_id': task['trace_context']['trace_id'], 'locations': locations,
    'active_execution_locks': 0, 'active_reservations': 0,
    'consumption_quantities_ml': sorted(c['actual_quantity'] for c in consumptions),
    'created_at': task['create_time'], 'updated_at': task['update_time'],
    'plc_event_sequence': plc['state']['sequence'],
}
save('summary', summary)
print('FULL_FLOW_VERIFIED', json.dumps(summary, ensure_ascii=False), flush=True)
