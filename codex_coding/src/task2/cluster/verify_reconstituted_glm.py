"""Verify full GLM generation and every restored byte after CPU-owner reconstruction."""
import argparse
from pathlib import Path
import resource
from run_recall_pressure_motivation import PressureRun


class VerifyRun(PressureRun):
    def run(self):
        self.result['protocol']['role'] = 'full-model verification after owner reconstruction'
        self.cluster.profile['serial_target_execution'] = True
        try:
            self.park('A'); self.park('B')
            row = self.resume('A', 'owner_reconstituted', verify=True)
            self.result['restored'] = row
            self.result['status'] = 'passed'
            self.event('full_glm_verified', first_token_s=row['request_to_first_token_s'],
                       checked_bytes=row['verification']['checked_bytes'])
        except BaseException as exc:
            self.result.update(status='failed', error=repr(exc)); self.event('failed_resources_retained', error=repr(exc))
            raise
        finally:
            self.checkpoint_lifecycle()


if __name__ == '__main__':
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--run', required=True)
    parser.add_argument('--route-result', type=Path, required=True)
    parser.add_argument('--resume-run', type=Path, required=True)
    parser.set_defaults(fractions=[1.], repeats=1, wait_timeout=10800, cpu_first_ssd=False,
                        resume_failed=False, free_pages_only=True)
    VerifyRun(parser.parse_args()).run()
