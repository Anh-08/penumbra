// MONTE CARLO grid (T38): state, tile assignment and outcome sampling. Pure: no DOM, no three, tested in Node.
// The grid is an illustration driven by the verifier's results. Pass/fail per tile is sampled from the backend's
// per_hyp.pass_rate with a seed taken from the plan hash, so the share of red tiles matches the verifier.
// Nothing here simulates power: the SOC bar follows a cue curve and ends at the sampled outcome.

export const TILE_COUNT = 20;
export const SAMPLES_PER_HYPOTHESIS = 100;   // backend/verify/constants.py MONTE_CARLO_N
export const FADE_MS = 1500;                 // fade out this long after an execution or a collapsed sample
// After the matrix is complete the grid stays this long so the judge can read it (was FADE_MS). While the
// satellite waits for the human's approval the grid is held open until the execution message arrives.
export const HOLD_MS = 6000;
// The agent often simulates several plans in one model turn. Each plan is shown for at least this long (the queue
// lives in the overlay only: the director never waits for it).
export const DWELL_MS = 1400;
export const OPEN_PHASES = ['agent', 'approval'];
export const ENABLED_KEY = 'penumbra.mcview.enabled';
export const HYPOTHESIS_ORDER = ['H0', 'H1', 'H2', 'H3', 'H4', 'H5'];
// The part that breaks under each hypothesis (anatomy part ids). H5 also tilts the Sun arrow.
export const FAULT_PART = { H0: null, H1: 'wings', H2: 'aux', H3: 'battery', H4: 'pcdu', H5: 'wings' };

export function loadEnabled(storage) {
  try { return storage?.getItem(ENABLED_KEY) !== '0'; } catch { return true; }
}

export function saveEnabled(storage, enabled) {
  try { storage?.setItem(ENABLED_KEY, enabled ? '1' : '0'); } catch { /* per-viewer convenience only */ }
}

// ---- seeded sampling ----------------------------------------------------------------------------------------

// FNV-1a over the string: a stable 32-bit seed from the plan hash.
export function seedFrom(text) {
  let h = 0x811c9dc5;
  for (const ch of String(text)) {
    h ^= ch.codePointAt(0);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h >>> 0;
}

// mulberry32: small, fast, good enough for picking tiles.
export function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// One hypothesis per tile, in proportion to the plausible set (the verifier runs the same number of samples for
// each plausible hypothesis). Tiles left over go to the most probable hypotheses. Grouped in H0..H5 order.
export function assignHypotheses(plausible, scores = {}, n = TILE_COUNT) {
  const set = [...new Set((plausible ?? []).filter((h) => HYPOTHESIS_ORDER.includes(h)))];
  const hyps = set.length ? set : ['H0'];
  const base = Math.floor(n / hyps.length);
  const byPosterior = [...hyps].sort((a, b) => (scores[b] ?? 0) - (scores[a] ?? 0)
    || HYPOTHESIS_ORDER.indexOf(a) - HYPOTHESIS_ORDER.indexOf(b));
  const count = Object.fromEntries(hyps.map((h) => [h, base]));
  for (let i = 0; i < n - base * hyps.length; i += 1) count[byPosterior[i]] += 1;
  return HYPOTHESIS_ORDER.filter((h) => count[h]).flatMap((h) => Array(count[h]).fill(h));
}

// 'pass' | 'fail' per tile, or 'unknown' where the backend sent no stats for that hypothesis.
// For a hypothesis with n tiles and pass rate p, the failing count is floor(n(1-p)) plus one more with probability
// equal to the remainder, so it is within one tile of n(1-p); which tiles fail is a seeded shuffle.
export function sampleOutcomes(result, hyps) {
  const out = hyps.map(() => 'unknown');
  for (const h of new Set(hyps)) {
    const p = result?.per_hyp?.[h]?.pass_rate;
    if (!Number.isFinite(p)) continue;
    const idx = hyps.flatMap((x, i) => (x === h ? [i] : []));
    const draw = rng(seedFrom(`${result.plan_hash ?? result.plan_id}:${h}`));
    const expected = idx.length * (1 - Math.min(1, Math.max(0, p)));
    const fails = Math.floor(expected) + (draw() < expected - Math.floor(expected) ? 1 : 0);
    for (let i = idx.length - 1; i > 0; i -= 1) {
      const j = Math.floor(draw() * (i + 1));
      [idx[i], idx[j]] = [idx[j], idx[i]];
    }
    idx.forEach((tile, k) => { out[tile] = k < fails ? 'fail' : 'pass'; });
  }
  return out;
}

// ---- cue curves (illustration only, no model) --------------------------------------------------------------

export const CYCLE_S = 4.0;            // one illustrative orbit per tile cycle
export const SUNLIT_FRACTION = { default: 0.62, H5: 0.45 };   // H5: the eclipse lasts longer
export const SETTLE_S = 1.2;           // after the result arrives the bar moves to its outcome in this time
export const SOC_LINE = 0.3;           // where the bar draws the limit line (a cue, not the backend limit)

export function sunlit(hyp, tSeconds) {
  const phase = ((tSeconds / CYCLE_S) % 1 + 1) % 1;
  return phase < (SUNLIT_FRACTION[hyp] ?? SUNLIT_FRACTION.default);
}

// Bar height in [0, 1]: charges in sunlight, drains in eclipse; after the outcome is known it eases to above the
// line (pass) or under it (fail). `resolvedFor` is the time since the outcome arrived, or null while running.
export function socCue(hyp, tSeconds, outcome = 'unknown', resolvedFor = null) {
  const phase = ((tSeconds / CYCLE_S) % 1 + 1) % 1;
  const sun = SUNLIT_FRACTION[hyp] ?? SUNLIT_FRACTION.default;
  const swing = hyp === 'H3' ? 0.3 : 0.18;
  const wave = phase < sun ? phase / sun : 1 - (phase - sun) / (1 - sun);
  const running = 0.5 + swing * (wave - 0.5);
  if (resolvedFor === null || outcome === 'unknown') return running;
  const end = outcome === 'fail' ? SOC_LINE * 0.35 : 0.55 + 0.15 * wave;
  const u = Math.min(1, Math.max(0, resolvedFor / SETTLE_S));
  const ease = u * u * (3 - 2 * u);
  return running + (end - running) * ease;
}

// ---- overlay state machine ----------------------------------------------------------------------------------

export function createMcState(enabled = true) {
  return { enabled, mode: '3d', phase: 'attract', agentSat: null, open: false, satId: null, planId: null,
    run: 0, startedAt: null, queue: [], results: {}, hypotheses: {}, complete: false, closeAt: null, expanded: null,
    held: false };
}

const key = (satId, planId) => `${satId}|${planId}`;

function close(state) {
  return { ...state, open: false, closeAt: null, expanded: null, queue: [], held: false };
}

function startRun(state, satId, planId, now) {
  return { ...state, open: true, satId, planId, run: state.run + 1, startedAt: now, complete: false, closeAt: null,
    expanded: null };
}

function withPhase(state, phase) {
  const next = { ...state, phase };
  return next.open && !OPEN_PHASES.includes(phase) ? close(next) : next;
}

function storeResults(state, satId, results) {
  if (!satId || !results?.length) return state;
  const store = { ...state.results };
  for (const r of results) if (r?.plan_id && r.per_hyp) store[key(satId, r.plan_id)] = r;
  return { ...state, results: store };
}

function onTrace(state, event, now) {
  const payload = event?.payload ?? {};
  const name = payload.name ?? payload.tool ?? payload.tool_name;
  const args = payload.args ?? {};
  if (event?.kind === 'tool_call') {
    let next = state.phase === 'storm' ? withPhase(state, 'agent') : state;
    if (typeof args.sat_id === 'string') next = { ...next, agentSat: args.sat_id };
    if (name === 'simulate' && typeof args.plan_id === 'string' && next.enabled && OPEN_PHASES.includes(next.phase)) {
      const satId = next.agentSat ?? next.satId;
      if (!next.open || satId !== next.satId) return startRun({ ...next, queue: [] }, satId, args.plan_id, now);
      if (args.plan_id !== next.planId && !next.queue.includes(args.plan_id)) {
        next = { ...next, queue: [...next.queue, args.plan_id], complete: false, closeAt: null };
      }
    }
    return next;
  }
  if (event?.kind === 'tool_result' && name === 'simulate') {
    return storeResults(state, state.satId ?? state.agentSat, [payload.result]);
  }
  return state;
}

// action: { type: 'message', message, now } | { type: 'phase', phase } | { type: 'tick', now }
//       | { type: 'toggle_chip' } | { type: 'mode', mode } | { type: 'expand', tile } | { type: 'collapse', now }
export function reduceMc(state, action) {
  switch (action?.type) {
    case 'toggle_chip': {
      const enabled = !state.enabled;
      return enabled ? { ...state, enabled } : close({ ...state, enabled });
    }
    case 'mode':
      return action.mode === '2d' || action.mode === '3d' ? { ...state, mode: action.mode } : state;
    case 'phase':
      return action.phase && action.phase !== state.phase ? withPhase(state, action.phase) : state;
    case 'expand':
      return state.open && Number.isInteger(action.tile) && action.tile >= 0 && action.tile < TILE_COUNT
        ? { ...state, expanded: action.tile } : state;
    case 'collapse':
      return { ...state, expanded: null,
        closeAt: state.open && state.complete && !state.held ? (action.now ?? 0) + FADE_MS : state.closeAt };
    case 'tick': {
      if (!state.open) return state;
      const dwelt = action.now - (state.startedAt ?? action.now) >= DWELL_MS;
      if (state.queue.length && dwelt && state.expanded === null) {
        const [planId, ...queue] = state.queue;
        return { ...startRun(state, state.satId, planId, action.now), queue,
          complete: state.complete, closeAt: state.closeAt };
      }
      return !state.queue.length && dwelt && state.closeAt !== null && state.expanded === null
        && action.now >= state.closeAt ? close(state) : state;
    }
    case 'message':
      return onMessage(state, action.message, action.now ?? 0);
    default:
      return state;
  }
}

function onMessage(state, message, now) {
  const data = message?.data;
  if (!message?.type || !data) return state;
  switch (message.type) {
    case 'demo_state':
      return withPhase(state, data.state);
    case 'alert':
      return state.phase === 'attract' ? withPhase(state, 'storm') : state;
    case 'approval_request': {  // the human decides now: hold the grid for this satellite until it executes
      const next = withPhase(state, 'approval');
      return next.open && data.sat_id === next.satId ? { ...next, held: true, closeAt: null } : next;
    }
    case 'execution':
      return state.open && state.held && data.sat_id === state.satId
        ? { ...state, held: false, closeAt: now + FADE_MS } : state;
    case 'hypotheses':
      return { ...state, hypotheses: { ...state.hypotheses, [data.sat_id]: data } };
    case 'trace_event':
      return onTrace(state, data, now);
    case 'matrix': {
      const next = storeResults(state, data.sat_id, data.results);
      if (data.complete && next.open && data.sat_id === next.satId && !next.complete) {
        return { ...next, complete: true, closeAt: next.held ? null : now + HOLD_MS };
      }
      return next;
    }
    default:
      return state;
  }
}

// ---- selectors ----------------------------------------------------------------------------------------------

export function currentResult(state) {
  return state.satId && state.planId ? state.results[key(state.satId, state.planId)] ?? null : null;
}

// Everything the grid draws for the plan being verified.
export function gridModel(state, labels = {}) {
  const result = currentResult(state);
  const hyp = state.hypotheses[state.satId];
  const plausible = result?.plausible?.length ? result.plausible : hyp?.plausible;
  const hyps = assignHypotheses(plausible, hyp?.scores);
  const outcomes = result ? sampleOutcomes(result, hyps) : hyps.map(() => 'unknown');
  const names = { ...labels, ...(hyp?.labels ?? {}) };
  return {
    satId: state.satId, planId: state.planId, run: state.run, result, complete: state.complete,
    verdict: result ? (result.passes_all ? 'PASSES ALL' : 'FAILS') : null,
    tiles: hyps.map((h, i) => ({ index: i, hyp: h, label: names[h] ?? h, fault: FAULT_PART[h] ?? null,
      outcome: outcomes[i] })),
  };
}

// The sample SHOW RESULT opens: a failing tile of the worst plausible hypothesis, else its first tile.
export function spotlightTile(model) {
  const r = model.result;
  if (!r || !model.tiles.length) return 0;
  const plausible = model.tiles.map((t) => t.hyp).filter((h) => Number.isFinite(r.per_hyp?.[h]?.min_soc_p5));
  const worst = plausible.reduce((w, h) => (w === null || r.per_hyp[h].min_soc_p5 < r.per_hyp[w].min_soc_p5 ? h : w),
    null);
  const pick = model.tiles.find((t) => t.hyp === worst && t.outcome === 'fail')
    ?? model.tiles.find((t) => t.outcome === 'fail') ?? model.tiles.find((t) => t.hyp === worst) ?? model.tiles[0];
  return pick.index;
}

// Backend numbers only: pass_rate and min_soc_p5 for this plan and hypothesis, the plan's worst_margin_soc and
// the posterior from the latest `hypotheses` message.
export function spotlightModel(state, model, tileIndex) {
  const tile = model.tiles[tileIndex];
  if (!tile) return null;
  const stats = model.result?.per_hyp?.[tile.hyp];
  const posterior = state.hypotheses[state.satId]?.scores?.[tile.hyp];
  return {
    ...tile, planId: model.planId, satId: model.satId,
    passRate: stats?.pass_rate ?? null, minSocP5: stats?.min_soc_p5 ?? null,
    worstMarginSoc: model.result?.worst_margin_soc ?? null,
    posterior: Number.isFinite(posterior) ? posterior : null,
  };
}
