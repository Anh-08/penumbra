// MONTE CARLO overlay (T38): twenty mini-satellites while PHYSICS VERIFIES. Lazy chunk (three loads here only).
// Driven by mcState (verifier results, sampled outcomes); the animation is an illustration, and says so.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import anatomy from '../data/satellite_anatomy.json';
import { gridModel, SAMPLES_PER_HYPOTHESIS, spotlightModel, spotlightTile, TILE_COUNT } from './mcState.js';
import './montecarlo.css';

const LABELS = { H0: 'Nominal', ...anatomy.hypotheses };
const OUTCOME_WORD = { pass: 'PASS', fail: 'FAIL', unknown: 'VERIFYING' };

const percent = (v) => (Number.isFinite(v) ? `${Math.round(v * 100)} %` : '—');
const signedPercent = (v) => (Number.isFinite(v) ? `${v >= 0 ? '+' : '−'}${Math.abs(v * 100).toFixed(1)} %` : '—');

function reducedMotion() {
  try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch { return false; }
}

function Spotlight({ spot, mode, onClose, legend, stageRef }) {
  const outcome = spot.outcome;
  return <section className={`mc-spot ${outcome}`} role="dialog" aria-modal="true"
    aria-label={`Monte Carlo sample under ${spot.hyp}`}>
    <header className="mc-spot-head">
      <div>
        <strong>Cause under this case: {spot.label}</strong>
        <span>{spot.hyp} · {spot.label} · one sample of {spot.planId} on {spot.satId}</span>
      </div>
      <span className="source-tag">SIM · ILLUSTRATIVE</span>
      <button type="button" className="mc-chip" onClick={onClose}>BACK TO GRID · ESC</button>
    </header>
    <div className="mc-spot-body">
      <div className={`mc-spot-stage ${mode}`} ref={stageRef} aria-hidden="true" />
      <dl className="mc-spot-facts">
        <div className={`mc-spot-outcome ${outcome}`}><dt>SAMPLED OUTCOME</dt><dd>{OUTCOME_WORD[outcome]}</dd></div>
        <div><dt>MONTE CARLO PASS RATE · {spot.hyp}</dt><dd className="num">{percent(spot.passRate)}</dd></div>
        <div><dt>MINIMUM SOC · FIFTH PERCENTILE · {spot.hyp}</dt><dd className="num">{percent(spot.minSocP5)}</dd></div>
        <div><dt>PLAN WORST MARGIN · SOC</dt><dd className="num">{signedPercent(spot.worstMarginSoc)}</dd></div>
        <div><dt>POSTERIOR · {spot.hyp}</dt><dd className="num">{percent(spot.posterior)}</dd></div>
        <p className="mc-spot-note">Numbers from the verifier and the hypothesis scores. The animation is an illustration.</p>
      </dl>
    </div>
    {legend && <div className="mc-spot-legend">{legend}</div>}
  </section>;
}

export default function MonteCarloOverlay({ state, dispatch, visible, legend }) {
  const canvas3d = useRef(null);
  const canvas2d = useRef(null);
  const gridRef = useRef(null);
  const tileRefs = useRef([]);
  const socRefs = useRef([]);
  const spotStage = useRef(null);
  const rendererRef = useRef(null);
  const outcomeAt = useRef({ run: -1, at: null });
  const [webgl, setWebgl] = useState(true);
  const [ready, setReady] = useState(false);
  const reduce = useMemo(reducedMotion, []);
  const model = useMemo(() => gridModel(state, LABELS), [state]);
  const mode = webgl ? state.mode : '2d';
  const expanded = state.expanded;
  const spot = expanded === null ? null : spotlightModel(state, model, expanded);

  // Renderer: created once, three imported lazily.
  useEffect(() => {
    let disposed = false;
    import('three').then(async (THREE) => {
      const { createMcRenderer } = await import('./mcRenderer.js');
      if (disposed || !canvas3d.current || !canvas2d.current) return;
      rendererRef.current = createMcRenderer(THREE, { canvas3d: canvas3d.current, canvas2d: canvas2d.current,
        reducedMotion: reduce });
      setWebgl(rendererRef.current.webgl);
      setReady(true);
    }).catch((error) => { console.warn('MC grid unavailable', error); setWebgl(false); });
    return () => { disposed = true; rendererRef.current?.dispose(); rendererRef.current = null; };
  }, [reduce]);

  // When the result for this run first arrives, the SOC bars start moving to their outcome.
  if (outcomeAt.current.run !== model.run) outcomeAt.current = { run: model.run, at: null };
  if (model.result && outcomeAt.current.at === null) outcomeAt.current.at = performance.now();

  useEffect(() => {
    rendererRef.current?.setModel(model, { runStartedAt: state.startedAt, outcomeAt: outcomeAt.current.at });
  }, [model, state.startedAt, ready]);
  useEffect(() => { rendererRef.current?.setMode(mode); }, [mode, ready]);
  useEffect(() => { rendererRef.current?.setExpanded(expanded); }, [expanded, ready]);

  // Layout: tile rectangles in viewport pixels (the canvases are fixed, full viewport).
  useLayoutEffect(() => {
    const measure = () => {
      const r = rendererRef.current;
      if (!r) return;
      const rectOf = (el) => {
        if (!el) return null;
        const b = el.getBoundingClientRect();
        return { x: b.left, y: b.top, w: b.width, h: b.height };
      };
      r.setLayout({ width: window.innerWidth, height: window.innerHeight,
        tiles: tileRefs.current.slice(0, TILE_COUNT).map(rectOf), spot: rectOf(spotStage.current) });
      if (reduce) r.frame(performance.now(), socRefs.current);
    };
    measure();
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(measure) : null;
    if (observer && gridRef.current) observer.observe(gridRef.current);
    window.addEventListener('resize', measure);
    return () => { observer?.disconnect(); window.removeEventListener('resize', measure); };
  }, [ready, expanded, visible, reduce, mode]);

  // Frame loop while visible (static redraws only, with reduced motion). The loop reads the renderer's current
  // model, so state changes never restart it.
  useEffect(() => {
    const r = rendererRef.current;
    if (!r || !visible) return undefined;
    let failed = false;
    const draw = (now) => {
      try { return r.frame(now, socRefs.current); } catch (error) {
        if (!failed) console.error('MC grid frame failed', error);
        failed = true;
        return null;
      }
    };
    try { window.__penumbraMc = { stats: r.stats, frame: () => draw(performance.now()) }; } catch { /* test hook */ }
    if (reduce) { draw(performance.now()); return undefined; }
    let id = 0;
    const loop = (now) => { draw(now); if (!failed) id = window.requestAnimationFrame(loop); };
    id = window.requestAnimationFrame(loop);
    return () => window.cancelAnimationFrame(id);
  }, [ready, visible, reduce]);

  // Static tiles (reduced motion): redraw when what they show changes.
  useEffect(() => {
    if (reduce && visible) rendererRef.current?.frame(performance.now(), socRefs.current);
  }, [reduce, visible, model, expanded, mode]);

  // Esc returns from the spotlight to the grid.
  useEffect(() => {
    if (expanded === null) return undefined;
    const onKey = (event) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopImmediatePropagation();
      dispatch({ type: 'collapse', now: performance.now() });
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [expanded, dispatch]);

  const header = <header className="mc-head">
    <div className="mc-title">
      <strong>MONTE CARLO</strong>
      <span>{TILE_COUNT} of {SAMPLES_PER_HYPOTHESIS} samples · illustrative animation driven by verifier results</span>
      <span className="source-tag">SIM</span>
    </div>
    <div className="mc-plan">
      <span className="mc-plan-id">{model.satId ?? '—'} · {model.planId ?? '—'}</span>
      {model.verdict && <b className={model.result?.passes_all ? 'pass' : 'fail'}>{model.verdict}</b>}
      <div className="mc-toggle" role="group" aria-label="Tile view">
        <button type="button" aria-pressed={mode === '3d'} disabled={!webgl}
          onClick={() => dispatch({ type: 'mode', mode: '3d' })}>3D</button>
        <button type="button" aria-pressed={mode === '2d'} onClick={() => dispatch({ type: 'mode', mode: '2d' })}>2D</button>
      </div>
      <button type="button" className="mc-chip" disabled={!model.result}
        onClick={() => dispatch({ type: 'expand', tile: spotlightTile(model) })}>SHOW RESULT</button>
    </div>
  </header>;

  return <div className={`mc-root ${visible ? 'visible' : ''} ${expanded !== null ? 'expanded' : ''}`}
    aria-hidden={!visible}>
    <div className="mc-dim" />
    <canvas ref={canvas3d} className={`mc-canvas ${mode === '3d' ? '' : 'off'}`} aria-hidden="true" />
    <canvas ref={canvas2d} className={`mc-canvas ${mode === '2d' ? '' : 'off'}`} aria-hidden="true" />
    <section className="mc-region" aria-label="Monte Carlo samples of the plan being verified">
      {header}
      <div className="mc-grid" ref={gridRef}>
        {model.tiles.map((tile, i) => <button type="button" key={`${model.run}:${i}`}
          ref={(el) => { tileRefs.current[i] = el; }}
          className={`mc-tile ${tile.outcome} ${reduce ? 'still' : ''}`}
          aria-label={`${tile.hyp} ${tile.label}: ${OUTCOME_WORD[tile.outcome]}`}
          onClick={() => dispatch({ type: 'expand', tile: i })}>
          <span className="mc-badge"><b>{tile.hyp}</b> {tile.label}</span>
          <span className="mc-soc" aria-hidden="true">
            <i ref={(el) => { socRefs.current[i] = { ...(socRefs.current[i] ?? {}), bar: el }; }} />
            <em />
          </span>
          <span className="mc-gauge" aria-hidden="true">
            <i ref={(el) => { socRefs.current[i] = { ...(socRefs.current[i] ?? {}), gauge: el }; }} />
          </span>
          <span className="mc-tip" role="tooltip">{tile.hyp} · {tile.label} · {OUTCOME_WORD[tile.outcome]}</span>
        </button>)}
      </div>
    </section>
    {spot && <Spotlight spot={spot} mode={mode} legend={legend} stageRef={spotStage}
      onClose={() => dispatch({ type: 'collapse', now: performance.now() })} />}
  </div>;
}
