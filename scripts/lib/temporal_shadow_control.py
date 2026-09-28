"""Private-file control of explicitly owned shadow processes in live tests."""
import json
import os
from pathlib import Path
import re
import signal
import time


class ShadowRecoveryControl:
    """Only the four frozen kill/restart actions are accepted; no caller PID input."""
    def __init__(self, directory, start_runtime, inventory, stop_group, started):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700)
        self.start_runtime, self.inventory, self.stop_group = start_runtime, inventory, stop_group
        self.started = started
        self.states, self.events, self.owned = [], [], set()
        self.expected = [(transport, action) for transport in ('isolated', 'session') for action in ('kill', 'restart')]
        self.failure = None
        self.closed = False

    def clock(self):
        return round((time.monotonic() - self.started) * 1000, 3)

    @property
    def state(self):
        return self.states[-1] if self.states else None

    def start(self):
        state = {'startedMs': self.clock()}
        self.states.append(state)
        # The callback stores the Popen handle before readiness or warmup can
        # fail, so close() also owns partially started runtimes.
        self.start_runtime(state)
        process = state['process']
        if process.poll() is not None:
            raise RuntimeError('Shadow runtime exited during controlled startup')
        self.owned.update(self.inventory(process.pid)[0])
        state['readyMs'] = self.clock()
        return state

    def sample(self):
        if self.state and 'process' in self.state:
            process = self.state['process']
            self.owned.update(self.inventory(process.pid)[0])
            planned_down = bool(self.events and self.events[-1]['action'] == 'kill')
            if not self.failure and not planned_down and process.poll() is not None:
                raise RuntimeError('Owned shadow runtime exited outside the planned outage')
        return set(self.owned)

    def poll(self, transport):
        if self.closed or self.failure or len(self.events) >= len(self.expected):
            return
        expected_transport, action = self.expected[len(self.events)]
        if transport != expected_transport:
            return
        name = f'{transport}-{action}'
        request_path = self.directory / f'{name}.request.json'
        if not request_path.exists():
            return
        event = {'action': action, 'transport': transport, 'startedMs': self.clock(), 'status': 'fail'}
        try:
            if request_path.is_symlink() or request_path.stat().st_size > 2048:
                raise ValueError('Invalid control request file')
            request = json.loads(request_path.read_text())
            if set(request) != {'schema', 'transport', 'action', 'instanceId'} or request['schema'] != 'agat.shadow.control.v1':
                raise ValueError('Invalid control request')
            if request['transport'] != transport or request['action'] != action or not re.fullmatch('[0-9a-f-]{36}', request['instanceId']):
                raise ValueError('Control request is outside the frozen sequence')
            if action == 'restart' and request['instanceId'] != self.events[-1]['instanceId']:
                raise ValueError('Control request changed workflow instance')
            event['instanceId'] = request['instanceId']
            process = self.state['process']
            event['oldPid'] = process.pid
            if action == 'kill':
                if process.poll() is not None:
                    raise RuntimeError('Runtime was already unavailable before injected failure')
                observed = self.inventory(process.pid)[0]
                self.owned.update(observed); event['ownedBeforeKill'] = sorted(observed)
                process.kill(); process.wait(timeout=5)
                if process.returncode != -signal.SIGKILL:
                    raise RuntimeError('Runtime did not terminate with SIGKILL')
                # Kill only the owning server. Its isolated child must retire
                # through the existing parent-watch/IPC EOF mechanism.
                deadline = time.monotonic() + 5
                while True:
                    remaining = observed & self.inventory(os.getpid())[1]
                    if not remaining or time.monotonic() >= deadline:
                        break
                    time.sleep(.05)
                event['remainingAfterKill'] = sorted(remaining)
                if remaining:
                    raise RuntimeError('Orphan shadow processes survived the owner')
                event['exitCode'] = process.returncode
            else:
                if process.poll() != -signal.SIGKILL:
                    raise RuntimeError('Restart requires the injected owner failure')
                state = self.start()
                event.update(newPid=state['process'].pid, runtimeIndex=len(self.states) - 1,
                             profileSha256=state['health']['profileSha256'])
            event['status'] = 'pass'
        except Exception as error:
            event['failure'] = {'type': type(error).__name__, 'reason': str(error)[:300]}
            self.failure = event['failure']
        event['finishedMs'] = self.clock()
        self.events.append(event)
        response = self.directory / f'{name}.response.json'
        # Publish atomically: the test must not observe a partially written ack.
        temporary = self.directory / f'{name}.response.tmp'
        with temporary.open('x') as stream:
            json.dump(event, stream, ensure_ascii=False, allow_nan=False)
        os.link(temporary, response)
        temporary.unlink()

    def close(self):
        if self.closed:
            return
        errors = []
        for state in reversed(self.states):
            process = state.get('process')
            if process is None:
                continue
            try:
                self.owned.update(self.inventory(process.pid)[0])
                self.stop_group(process)
            except Exception as error:
                errors.append(type(error).__name__)
        self.closed = True
        if errors:
            raise RuntimeError('Owned shadow cleanup failed: ' + ','.join(errors))

    def report(self):
        return {'schema': 'agat.shadow.control-result.v1', 'events': self.events, 'failure': self.failure,
                'closed': self.closed, 'ownedPids': sorted(self.owned),
                'runtimes': [{**{key: value for key, value in state.items() if key != 'process'},
                              'pid': state['process'].pid, 'exitCode': state['process'].returncode}
                             for state in self.states if 'process' in state]}
