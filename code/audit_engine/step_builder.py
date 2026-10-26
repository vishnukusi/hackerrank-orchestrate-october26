"""
Step builder and serializer for HackerRank Orchestrate output schema.
Maintains strict phase dependency and operation priorities:
1. FINDING -> REMEDIATION -> VERIFICATION
2. Operation dependencies:
   - DISABLE_DATA_FLOW before other remediations
   - BLOCK_RESTORE before deletions
   - DELETE_RECORD -> REDACT_RECORD -> ANONYMIZE_RECORD
3. Deterministic alphabetical sub-sorting by (system_id, resource_id).
"""

from typing import Any, Dict, List

FINDING_PRIORITY = {
    'REPORT_REMAINING_RECORD': 1,
    'REPORT_EXPOSED_PII': 2,
    'REPORT_ACTIVE_DATA_FLOW': 3,
    'REPORT_RESTORE_RISK': 4
}

REMEDIATION_PRIORITY = {
    'DISABLE_DATA_FLOW': 1,
    'BLOCK_RESTORE': 2,
    'DELETE_RECORD': 3,
    'REDACT_RECORD': 4,
    'ANONYMIZE_RECORD': 5
}

VERIFICATION_PRIORITY = {
    'VERIFY_RECORD_ABSENT': 1,
    'VERIFY_RECORD_PRESENT': 2,
    'VERIFY_RECORD_REDACTED': 3,
    'VERIFY_RECORD_ANONYMIZED': 4,
    'VERIFY_DATA_FLOW_DISABLED': 5,
    'VERIFY_RESTORE_BLOCKED': 6
}

PHASE_PRIORITY = {
    'FINDING': 1,
    'REMEDIATION': 2,
    'VERIFICATION': 3
}


def step_sort_key(step: Dict[str, Any]):
    phase = step.get('phase', '')
    op = step.get('operation', '')
    sys_id = step.get('system_id', '')
    res_id = step.get('resource_id', '')

    p_prio = PHASE_PRIORITY.get(phase, 99)
    if phase == 'FINDING':
        op_prio = FINDING_PRIORITY.get(op, 99)
    elif phase == 'REMEDIATION':
        op_prio = REMEDIATION_PRIORITY.get(op, 99)
    else:
        op_prio = VERIFICATION_PRIORITY.get(op, 99)

    return (p_prio, op_prio, sys_id, res_id)


class StepBuilder:
    def __init__(self):
        self.steps: List[Dict[str, Any]] = []

    def add_step(
        self,
        phase: str,
        system_id: str,
        resource_id: str,
        operation: str,
        state: str,
        evidence_id: str = ""
    ) -> None:
        self.steps.append({
            "phase": phase,
            "system_id": system_id,
            "resource_id": resource_id,
            "operation": operation,
            "state": state,
            "evidence_id": evidence_id
        })

    def build(self) -> List[Dict[str, Any]]:
        """
        Sorts steps by phase, dependency/operation priority, and (system_id, resource_id).
        Assigns sequential 1-indexed step_no and returns formatted step dictionaries.
        """
        sorted_raw = sorted(self.steps, key=step_sort_key)
        final_steps = []
        for idx, item in enumerate(sorted_raw, start=1):
            final_steps.append({
                "step_no": idx,
                "phase": item["phase"],
                "system_id": item["system_id"],
                "resource_id": item["resource_id"],
                "operation": item["operation"],
                "state": item["state"],
                "evidence_id": item["evidence_id"]
            })
        return final_steps
