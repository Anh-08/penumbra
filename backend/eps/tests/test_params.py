import os
from pathlib import Path

import numpy as np
import pytest

from backend.eps import fit_pcoe
from backend.eps.params import PCOE, TRUTH, EpsParams

PCOE_DIR = Path(os.environ.get("PENUMBRA_PCOE_DIR", fit_pcoe.DEFAULT_DIR))


def test_every_parameter_has_a_source():
    for name, p in TRUTH.items():
        assert p.source.strip(), name
        assert p.source.startswith("assumed") or p.source.startswith("fitted") or "20" in p.source, name
        assert p.unit, name


def test_overrides_and_unknown_keys():
    assert EpsParams(panel_area=0.1).panel_area == 0.1
    with pytest.raises(KeyError):
        EpsParams(not_a_param=1)


def test_ocv_curve_is_monotonic_and_plausible():
    p = EpsParams()
    ocv = np.asarray(p.ocv_cell_v)
    assert len(ocv) == len(p.soc_grid)
    assert np.all(np.diff(ocv) > 0)
    assert 2.5 < ocv[0] < 3.3 and 4.1 < ocv[-1] < 4.3


@pytest.mark.skipif(not fit_pcoe.available(PCOE_DIR), reason="data/pcoe not present")
def test_pcoe_fit_matches_recorded_params():
    pytest.importorskip("scipy")
    fit = fit_pcoe.fit(PCOE_DIR)
    p = EpsParams()
    assert np.allclose(fit["ocv_cell_v"], p.ocv_cell_v, atol=1e-3)
    assert fit["r0_cell_bol_ohm"] == pytest.approx(p.r0_cell_ohm, abs=1e-3)
    assert fit["r0_cell_eol_ohm"] == pytest.approx(p.r0_cell_eol_ohm, abs=1e-3)
    assert fit["coulombic_eff"] == pytest.approx(p.coulombic_eff, abs=1e-3)
    assert fit["fade_per_cycle"] == pytest.approx(p.fade_per_cycle, rel=1e-2)
    assert fit["cap_first_ah"] == pytest.approx(p.cell_capacity_ah, abs=1e-3)
    for name in ("ocv_cell_v", "r0_cell_ohm", "coulombic_eff", "fade_per_cycle", "cell_capacity_ah"):
        assert TRUTH[name].source.startswith(PCOE)
