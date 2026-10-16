"""Optional page-job attribution around existing GFX scheduling.

Owns no executor, cache or sink. Page elapsed includes queueing and all block
completion/validation; it is distinct from each render_page invocation.
"""
import threading
from . import events


def _begin(task):
    # Do not bind a ContextVar across threads. The existing block.call binds its
    # own context on worker entry; all timing belongs to the shared clock.
    task.started = task.execution.clock()
    task.execution.emit('task_start', task=task, scope=task.kind,
                        started=task.started, extensions=task.metadata)
    return task


class PageJobs:
    def __init__(self, jobs):
        self.lock = threading.Lock()
        self.blocks = {}
        groups = {}
        for job in jobs:
            block = job[7]
            page_id = block.metadata['page_id']
            groups.setdefault(page_id, []).append(job)
        validation_groups = {}
        for page_id, members in groups.items():
            first = members[0]
            # Mirror the existing CLI's real completion owner. Split occurrences
            # sharing (cid, key) aggregate into one validation; do not invent an
            # earlier per-occurrence success or change cache/scheduling policy.
            owner = ('split', first[0], first[1]) if first[6] is not None else ('whole', page_id)
            state = validation_groups.setdefault(owner, {'pages': [], 'remaining': 0,
                'code': 0, 'error': None, 'id': str(events.uuid.uuid4())})
            state['remaining'] += len(members)
            block = first[7]
            execution = block.execution
            page = execution.task('gfx', metadata={'operation': 'page_job',
                'page_id': page_id, 'gfx_key': first[1], 'frame_range': [0, first[3]],
                'validation_group_id': state['id']})
            _begin(page)
            state['pages'].append(page)
            for job in members:
                block = job[7]
                block.parent_task_id = page.task_id
                queued = execution.task('gfx', parent=page,
                    metadata={**block.metadata, 'operation': 'queue'})
                _begin(queued)
                self.blocks[block.task_id] = (state, queued)

    def call(self, fn, job):
        block = job[7]
        state, queued = self.blocks[block.task_id]
        queued.finish(0)
        code, error = 0, None
        try:
            return block.call(fn, job)
        except BaseException as caught:
            code, error = events.exit_code(caught), caught
            raise
        finally:
            with self.lock:
                if code:
                    state['code'], state['error'] = code, error
                state['remaining'] -= 1
                if state['remaining'] == 0:
                    for page in state['pages']:
                        page.finish(state['code'], state['error'])
