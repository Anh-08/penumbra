// Small attract-screen entry to the SATELLITE ANATOMY view (T37): a tiny rotating skeleton. Lazy-loaded after the
// landing page has painted, so three never weighs on the first paint.
import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import table from '../data/satellite_anatomy.json';
import { createSatelliteSkeleton } from './skeleton.js';
import './anatomy.css';

export default function AnatomyCard({ onOpen }) {
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const reduced = typeof window.matchMedia === 'function'
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let renderer;
    try {
      renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    } catch { return undefined; }  // no WebGL: the card still opens the view, which says so
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(canvas.clientWidth || 104, canvas.clientHeight || 64, false);
    const scene = new THREE.Scene();
    const ink = getComputedStyle(document.documentElement).getPropertyValue('--c-ink').trim() || '#e9eff7';
    const sat = createSatelliteSkeleton(THREE, { detail: 'simple', ink, baseOpacity: 0.9 });
    scene.add(sat.group);
    const camera = new THREE.PerspectiveCamera(30, (canvas.clientWidth || 104) / (canvas.clientHeight || 64), 0.1, 50);
    camera.position.set(4.2, 2.4, 6.0);
    camera.lookAt(0, -0.05, 0);
    let raf = 0;
    let last = 0;
    const frame = (now) => {
      if (now - last > 33) {  // about 30 fps is plenty for a thumbnail
        sat.group.rotation.y = reduced ? -0.5 : now / 4000;
        renderer.render(scene, camera);
        last = now;
      }
      if (!reduced) raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => { cancelAnimationFrame(raf); sat.dispose(); renderer.dispose(); };
  }, []);

  return <button type="button" className="hud-panel anatomy-card" onClick={onOpen}
    aria-label="Open the satellite anatomy view (A)">
    <canvas ref={canvasRef} aria-hidden="true" />
    <span className="anatomy-card-text"><strong>{table.class_label} · explore</strong>
      <span>ANATOMY · press A · illustrative</span></span>
  </button>;
}
