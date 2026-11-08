import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { STAGE_LABELS, STEPPER_LABEL, approvalTierLine, stepperItems } from './operatorCopy.js';

const source = (name) => readFileSync(new URL(name, import.meta.url), 'utf8');

test('the stepper reads as progress: three stages with an arrow between each pair', () => {
  assert.deepEqual(STAGE_LABELS, ['AI PLAN', 'VERIFICATION', 'APPROVAL']);
  assert.equal(STEPPER_LABEL, 'Progress: AI plan, verification, approval');
  const items = stepperItems('incident', false);
  assert.deepEqual(items.map((item) => item.kind), ['stage', 'arrow', 'stage', 'arrow', 'stage']);
  assert.deepEqual(items.filter((item) => item.kind === 'stage').map((item) => item.label), STAGE_LABELS);
  const panels = source('./Panels.jsx');
  assert.match(panels, /className=\{`stage-arrow[^`]*`\} aria-hidden="true"/);
  assert.match(panels, /aria-label=\{STEPPER_LABEL\}/);
  assert.doesNotMatch(panels, /AI PROPOSES|PHYSICS VERIFIES|HUMAN APPROVES/);
});

test('the active stage and the arrow into it advance together; executed means all done', () => {
  const states = (phase, verified) => stepperItems(phase, verified).map((item) => item.state);
  assert.deepEqual(states('incident', false), ['active', '', '', '', '']);
  assert.deepEqual(states('incident', true), ['', 'active', 'active', '', '']);
  assert.deepEqual(states('approval', true), ['', '', '', 'active', 'active']);
  assert.deepEqual(states('fastforward', true), ['done', 'done', 'done', 'done', 'done']);
  assert.deepEqual(states('reveal', true), ['done', 'done', 'done', 'done', 'done']);
  assert.equal(stepperItems('approval', true).at(-1).last, true);
});

test('a tier 2 ticket asks for the operator; tier 1 keeps its tier', () => {
  assert.equal(approvalTierLine(2, 'ab12cd'), 'NEEDS YOUR APPROVAL · HASH ab12cd');
  assert.equal(approvalTierLine(1, 'ab12cd'), 'TIER 1 · HASH ab12cd');
});

test('operator wording replaces the demo wording in the panels', () => {
  const text = ['./Panels.jsx', './IncidentPicker.jsx', './FlightOverlay.jsx', './SocChart.jsx'].map(source).join('\n');
  for (const label of ['START INCIDENT', 'STARTING…', 'SHOW WHAT HAPPENED', 'SHOWING…', '>ALERTS<', 'NEXT PASS ',
    'TIME LEFT ', "'WHAT HAPPENED'", 'Eclipse complete. Show what happened to the battery.', 'AI ASSESSMENT',
    'ORIGINAL INCIDENT', '— actual battery', 'p5–p95 verifier band']) {
    assert.ok(text.includes(label), label);
  }
  for (const old of ['FAULT STORM', 'REVEAL TRUTH', 'REVEALING…', 'TRIAGE QUEUE', 'AOS {', 'TTC {', 'AGENT DIAGNOSED',
    'TRUE FAULT', 'downlinked only', 'simulated truth', "'REVEAL'"]) {
    assert.ok(!text.includes(old), old);
  }
});
