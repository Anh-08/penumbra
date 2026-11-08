import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const css = readFileSync(new URL('./styles.css', import.meta.url), 'utf8');
const rule = (selector) => css.match(new RegExp(`^${selector.replace('.', '\\.')} \\{([^}]*)\\}`, 'm'))?.[1] ?? '';

test('the top bar centres the stepper between two equal side columns (T45)', () => {
  assert.match(rule('.top-bar'), /display: grid; grid-template-columns: 1fr auto 1fr; align-items: center;/);
  assert.doesNotMatch(rule('.top-bar'), /justify-content: space-between/);
  assert.match(rule('.top-brand'), /grid-column: 1; justify-self: start;/);
  assert.match(rule('.stage-stepper'), /grid-column: 2; justify-self: center;/);
  assert.match(rule('.top-status'), /grid-column: 3; justify-self: end;/);
});
