# `anatomy`: wireframe satellite skeleton and power-path animation

A small, reusable module for the illustrative bus (T37, reused by T38's Monte Carlo grid). It draws a
blueprint-style wireframe (EdgesGeometry line segments, no textures, no external files) and animates current as
pulses along right-angle paths. It is an illustration: no numbers come out of it.

`three` is passed in, so callers lazy-load it and tests run headless in Node.

```js
const THREE = await import('three');
import { createSatelliteSkeleton, animatePowerFlow } from './anatomy/index.js';

const sat = createSatelliteSkeleton(THREE, { detail: 'full' /* or 'simple' */, ink: '#d6e0ea', accent: '#ffb020' });
scene.add(sat.group);

sat.setHighlight('battery');   // light one part, dim the rest; null clears
sat.anchor('battery');         // world-space THREE.Vector3 for a leader line (also 'wing_py', 'wing_my')
sat.pickables;                 // meshes for raycasting; mesh.userData.partId
sat.setPartOn('aux', false);   // draw a part as switched off

const flow = animatePowerFlow(THREE, sat, { sunlit: true, loadsOn: { PAYLOAD: true, HEATER: true, AUX: false } });
flow.update(dtSeconds);        // move the pulses (call every frame)
flow.setSunlit(false);         // eclipse: battery -> PCDU -> loads
flow.setLoad('PAYLOAD', false); // a non-essential load drops out of the path; BUS/COMMS/ADCS refuse (returns false)
flow.dispose(); sat.dispose();
```

2D schematic (T38's 2D / paper tiles): a minimal front cross-section as plain data.

```js
import { createSchematic, schematicSvg, drawSchematic, faultDot, FAULT_PART } from './anatomy/index.js';
const sch = createSchematic();                    // { width: 120, height: 80, shapes, anchors, parts }
el.innerHTML = schematicSvg(sch, { fault: 'H3' }); // red battery outline + red dot on the faulty part
drawSchematic(ctx, sch, { x, y, scale, fault: 'H1' }); // the same into a Canvas 2D context
FAULT_PART;  // H1/H5 -> wings, H2 -> aux, H3 -> battery, H4 -> pcdu, H0 -> null
```

- Part ids (`PART_IDS`): bus, wings, battery, pcdu, obc, adcs, star_tracker, gnss, sband, payload, heater, aux,
  propulsion. `'simple'` keeps `SIMPLE_PART_IDS` (structure, power chain and the switchable loads) for small tiles.
- EPS load → part (`LOAD_PARTS`): BUS → obc, COMMS → sband, ADCS → adcs, PAYLOAD → payload, HEATER → heater,
  AUX → aux. Load names are the ones in `backend/eps/params.py`.
- Descriptions, load classes, fault hypotheses and the few numbers shown live in
  `frontend/src/data/satellite_anatomy.json` (`"illustrative": true`, every number with its `params.py` source).
- The bus is illustrative: a generic LEO smallsat with the EPS this project simulates, not any operator's design.
