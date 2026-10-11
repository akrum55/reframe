"""Exercise the actual polling-loop body without importing hardware modules."""
import ast
import copy
from pathlib import Path
import types
import unittest

from button_gestures import ButtonGestureRecognizer


class TraceSpy:
    def __init__(self):
        self.samples = []
        self.actions = []

    def observe(self, **fields):
        self.samples.append({k: v for k, v in fields.items() if k != 'gesture'})

    def action(self, **fields):
        self.actions.append(fields)


def polling_step(namespace):
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'reframe.py').read_text())
    loops = [node for node in ast.walk(tree) if isinstance(node, ast.While)
             and any(isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                     and child.func.id == 'is_power_button_pressed'
                     for child in ast.walk(node))]
    assert len(loops) == 1
    body = copy.deepcopy(loops[0].body)
    assert isinstance(body[-1], ast.Expr) and body[-1].value.func.id == 'sleep'
    body.pop()
    fn = ast.FunctionDef(name='step', args=ast.arguments(posonlyargs=[], args=[],
        kwonlyargs=[], kw_defaults=[], defaults=[]), body=body, decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))
    exec(compile(module, '<real-button-loop>', 'exec'), namespace)
    return namespace['step']


class PollingTraceIntegrationTests(unittest.TestCase):
    def run_trace(self, readings, enabled, reject=False):
        context = {'now': 0, 'raw': False, 'busy': False}
        calls = {'read': 0, 'busy': 0, 'submitted': []}
        def read():
            calls['read'] += 1
            return context['raw']
        def busy():
            calls['busy'] += 1
            return context['busy']
        def submit(action):
            calls['submitted'].append(action)
            return not reject
        trace = TraceSpy() if enabled else None
        namespace = {'is_power_button_pressed': read,
            'button_trace': trace, 'time': types.SimpleNamespace(monotonic=lambda: context['now']),
            'button_gestures': ButtonGestureRecognizer(),
            'button_actions': types.SimpleNamespace(is_busy=busy, submit=submit),
            'logging': types.SimpleNamespace(info=lambda *a: None),
            'update_diagnostics': lambda **kw: None}
        step = polling_step(namespace)
        for now, raw, is_busy in readings:
            context.update(now=now, raw=raw, busy=is_busy)
            step()
        self.assertEqual(calls['read'], len(readings))
        self.assertEqual(calls['busy'], len(readings))
        if trace:
            self.assertEqual(len(trace.samples), len(readings))
            self.assertEqual([x['raw'] for x in trace.samples], [x[1] for x in readings])
            self.assertEqual([x['emitted'] for x in trace.actions], calls['submitted'])
        return calls['submitted'], trace

    @staticmethod
    def samples(presses, fault_at=None, busy_until=None):
        result = []
        for tick in range(100):
            now = tick * .026
            raw = any(start <= now < end for start, end in presses)
            if fault_at is not None and abs(now - fault_at) < .013:
                raw = None
            result.append((now, raw, busy_until is not None and now < busy_until))
        return result

    def test_single_double_triple_actions_identical_with_and_without_trace(self):
        for count, expected in ((1, ['capture']), (2, []), (3, ['qr'])):
            readings = self.samples([(.26 + i * .26, .39 + i * .26) for i in range(count)])
            with self.subTest(count=count):
                for enabled in (False, True):
                    actions, _ = self.run_trace(readings, enabled)
                    self.assertEqual(actions, expected)

    def test_busy_inputs_are_still_discarded(self):
        readings = self.samples([(.26, .39), (.52, .65), (.78, .91)], busy_until=1.2)
        for enabled in (False, True):
            self.assertEqual(self.run_trace(readings, enabled)[0], [])

    def test_unknown_read_cancels_same_sequence(self):
        readings = self.samples([(.26, .39), (.52, .65), (.78, .91)], fault_at=.572)
        for enabled in (False, True):
            self.assertEqual(self.run_trace(readings, enabled)[0], [])

    def test_late_taps_keep_same_expiration_behavior(self):
        readings = self.samples([(.26, .39), (1.04, 1.17), (1.82, 1.95)])
        for enabled in (False, True):
            self.assertEqual(self.run_trace(readings, enabled)[0], ['capture'] * 3)

    def test_rejected_dispatch_is_recorded_without_resubmission(self):
        readings = self.samples([(.26, .39), (.52, .65), (.78, .91)])
        actions, trace = self.run_trace(readings, True, reject=True)
        self.assertEqual(actions, ['qr'])
        self.assertFalse(trace.actions[0]['submitted'])

    def test_long_hold_still_does_not_dispatch(self):
        for enabled in (False, True):
            self.assertEqual(self.run_trace(self.samples([(.26, 2.4)]), enabled)[0], [])


if __name__ == '__main__':
    unittest.main()
