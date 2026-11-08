"""Main-only host adapter for one persistent four-request outer calibration."""
import argparse
import json
from pathlib import Path

from simple_host import SimpleHostOps, WORKFLOW
from orchestrate import HERE, RESULTS, reference, thread_exec_env, write

PROTOCOL = 'post_prime_outer_protocol_v1'


def require_outer(plan):
    protocol = plan.get('outer_protocol', {})
    if (plan.get('workflow') != WORKFLOW or len(plan['processes']) != 4
            or protocol.get('schema') != PROTOCOL
            or protocol.get('complete_request_order') != ['C0_prime', 'C0_cal', 'Hremote', 'Hnear']
            or protocol.get('validation_order') != ['Hremote', 'Hnear']
            or protocol.get('max_complete_requests') != 4
            or protocol.get('outer_domain') != 'post_one_fullrestore_samecontroller'
            or plan['phase_deadlines_s'].get('sequence') != 3600):
        raise ValueError('exact persistent-controller outer protocol required')


class OuterHostOps(SimpleHostOps):
    RUN_PHASES = ('bootstrap', 'sequence')

    def controller(self, plan, remote, action):
        require_outer(plan)
        if action not in ('init', 'bootstrap', 'sequence', 'cleanup'):
            raise ValueError('separate calibrate/validate processes are forbidden')
        deadline = plan['phase_deadlines_s'][action]
        self.journal(action, 'begin', deadline_s=deadline,
                     complete_request_order=plan['outer_protocol']['complete_request_order'] if action == 'sequence' else [])
        runtime = Path(plan['handoff']).parent
        argv = ['python3', str(HERE / 'outer_controller.py'), '--plan', str(remote), '--action', action,
                '--out', str(runtime / ('phase_' + action)), '--deadline-s', str(deadline), '--execute']
        if action == 'bootstrap':
            argv += ['--binding', str(runtime / 'phase_init/init.json')]
        if action in ('sequence', 'cleanup'):
            argv += ['--bootstrap', str(runtime / 'phase_bootstrap/bootstrap.json')]
        self.record('node27', ['docker', 'exec', *thread_exec_env(), plan['nodes']['node27']['container_id'], *argv],
                    'controller_' + action, deadline + 15)
        self.journal(action, 'complete',
                     persistent_controller_sequence=action == 'sequence',
                     separate_validate_process_executed=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('setup', 'admission', 'bind', 'run', 'collect'))
    parser.add_argument('--setup-dir', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    for name in ('bindings', 'admission', 'plan'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--main-approved', action='store_true')
    args = parser.parse_args()
    out = args.out.resolve()
    if out.parent != RESULTS or not out.name.startswith('execution_host_outer_'):
        raise ValueError('fresh execution_host_outer_* output required')
    if args.action != 'bind' and not args.execute:
        print(json.dumps(dict(status='not_executed', action=args.action)))
        return
    plan = json.loads((args.setup_dir / 'launch_plan.unbound.json').read_text())
    require_outer(plan)
    out.mkdir(parents=True, exist_ok=False)
    if args.action == 'bind':
        from bind_plan import bind
        plan = bind(plan, json.loads(args.bindings.read_text()), json.loads(args.admission.read_text()), args.main_approved)
        plan.update(scope='fresh four-process single-sender persistent-controller outer validation; native lifecycle mandatory',
                    budget_status='explicit_simple_helper_planned2GiB_freeguard_verified')
        plan['binding_source_refs'] = {name: reference(getattr(args, name)) for name in ('bindings', 'admission')}
        require_outer(plan)
        write(out / 'launch_plan.json', plan)
        print(json.dumps(dict(status='bound_plan_only', plan=str(out / 'launch_plan.json'))))
        return
    ops = OuterHostOps(args.setup_dir, out)
    try:
        if args.action == 'setup':
            ops.setup(args.setup_dir)
        elif args.action == 'admission':
            ops.admission(json.loads(args.bindings.read_text()))
        else:
            plan = json.loads(args.plan.read_text())
            require_outer(plan)
            if args.action == 'run':
                ops.run(args.plan, plan)
            else:
                ops.collect(plan)
    except BaseException as exc:
        ops.journal(args.action, 'aborted', failure=repr(exc))
        raise


if __name__ == '__main__':
    main()
