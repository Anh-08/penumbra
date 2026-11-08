// Operator wording for the top-bar progress stepper and the approve button (T41). Pure, so node --test covers it.

export const STAGE_LABELS = ['AI PLAN', 'VERIFICATION', 'APPROVAL'];
export const STEPPER_LABEL = 'Progress: AI plan, verification, approval';

// Stage states as before T41: all 'done' once the plan is executed (fast-forward, reveal), otherwise only the
// current stage is 'active'. An arrow takes the state of the stage it leads into, so it fills together with it.
export function stepperItems(phase, hasVerification) {
  const complete = phase === 'fastforward' || phase === 'reveal';
  const active = phase === 'approval' || complete ? 2 : hasVerification ? 1 : 0;
  const last = STAGE_LABELS.length - 1;
  const items = [];
  STAGE_LABELS.forEach((label, index) => {
    const state = complete ? 'done' : index === active ? 'active' : '';
    if (index > 0) items.push({ kind: 'arrow', key: `arrow-${index}`, state, last: index === last });
    items.push({ kind: 'stage', key: label, label, state, last: index === last });
  });
  return items;
}

// The small line under APPROVE: a tier 2 ticket needs the operator, tier 1 keeps its tier.
export function approvalTierLine(tier, hash) {
  return tier === 2 ? `NEEDS YOUR APPROVAL · HASH ${hash}` : `TIER ${tier} · HASH ${hash}`;
}
