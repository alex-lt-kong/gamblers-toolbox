"""End-to-end chart test: serves the app in-process (uvicorn thread) against a
seeded DB and drives the real Chart.js dashboard with headless Chromium.

This is the regression net for the chart JS, which Python unit tests can't reach
(it's how a no-op `segmentBreak` once shipped). Skipped automatically when
Playwright or its Chromium aren't installed, so the default suite stays light:
    pip install playwright && python -m playwright install chromium
"""
import socket
import threading
import time
from datetime import date, timedelta

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright  # noqa: E402

import uvicorn  # noqa: E402

from core import config as host_config, main  # noqa: E402
from core.registry import discover_modules  # noqa: E402
from modules.pe_monitor import config as pe_config, storage, views  # noqa: E402


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _seed(db):
    """AAA: trailing loss days 30-59 (-> a TTM/blue band). BBB: always profitable
    (-> no bands). Forward/IBES stay positive for both, so AAA shows exactly one
    band kind and BBB none."""
    base = date(2024, 1, 1)
    for i in range(90):
        d = (base + timedelta(days=i)).isoformat()
        price = 100.0 + i * 0.5
        loss = 30 <= i < 60
        storage.append_snapshot(db, "AAA", {
            "date": d, "name": "Alpha Co", "currency": "USD", "price": price,
            "volume": 1_000_000 + i * 1000,
            "trailing_eps": -1.0 if loss else 3.0,
            "ttm_pe": None if loss else price / 3.0,
            "forward_eps": 8.0, "forward_pe": price / 8.0, "analyst_count": 5,
            "financial_currency": "USD", "forward_eps_native": None,
            "forward_pe_ibes": price / 8.0, "last_crawl_at": None})
        storage.append_snapshot(db, "BBB", {
            "date": d, "name": "Beta Co", "currency": "USD", "price": price,
            "volume": 2_000_000 + i * 1000,
            "trailing_eps": 4.0, "ttm_pe": price / 4.0,
            "forward_eps": 9.0, "forward_pe": price / 9.0, "analyst_count": 5,
            "financial_currency": "USD", "forward_eps_native": None,
            "forward_pe_ibes": price / 9.0, "last_crawl_at": None})


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    db = str(tmp_path_factory.mktemp("e2e") / "pe.db")
    storage.init_db(db)
    _seed(db)
    test_cfg = {"database_path": db, "tickers": ["AAA", "BBB"],
                "first_live_collection_date": "2024-02-15",
                "fetch_interval_seconds": 86400}
    orig = pe_config.load_config
    pe_config.load_config = lambda *a, **k: dict(test_cfg)
    views._cfg.cache_clear()

    cfg = host_config.load_config().model_copy(update={"enable_schedulers": False})
    modules = [m for m in discover_modules() if m.slug == "pe-monitor"]
    app = main.build_app(cfg, modules)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        pe_config.load_config = orig
        views._cfg.cache_clear()


def test_chart_aligns_and_shades_losses(live_server):
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch()
    except Exception as e:  # browser not installed in this environment
        pytest.skip(f"Chromium unavailable: {e}")
    page_errors = []
    try:
        page = browser.new_context(viewport={"width": 1100, "height": 1200}).new_page()
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.goto(f"{live_server}/pe-monitor/", wait_until="networkidle")
        page.wait_for_function("typeof gridApi !== 'undefined' && gridApi", timeout=15000)
        page.evaluate("() => { const w = new Set(['AAA', 'BBB']);"
                      " gridApi.forEachNode(n => { if (w.has(n.data.ticker)) n.setSelected(true); }); }")
        # Wait for deterministic chart state (both charts built and laid out) rather
        # than a fixed sleep, so a slow CI fetch/render can't make this read too early.
        page.wait_for_function(
            "() => typeof chartInstances !== 'undefined' && chartInstances.size === 2"
            " && [...chartInstances.values()].every(i => i.price && i.pe && i.vol"
            "      && i.price.scales.x.right > i.price.scales.x.left"
            "      && i.pe.scales.x.right > i.pe.scales.x.left"
            "      && i.vol.scales.x.right > i.vol.scales.x.left)",
            timeout=15000)
        diag = page.evaluate("""() => {
          const o = {};
          for (const [t, inst] of chartInstances) {
            const x = inst.pe.scales.x, vx = inst.vol.scales.x, px = inst.price.scales.x;
            o[t] = {price: [Math.round(px.left), Math.round(px.right)],
                    pe: [Math.round(x.left), Math.round(x.right)],
                    vol: [Math.round(vx.left), Math.round(vx.right)],
                    bands: inst.pe.options.plugins.lossBands.bands.length};
          }
          return o;
        }""")
        # Panel toggle regression: clicking "Volume" hides the volume panels, strikes the
        # chip, AND re-homes the date axis to the now-bottom-most visible panel (P/E). The
        # last part guards the regression where hiding Volume — the only panel showing date
        # labels — left every visible chart with no time axis at all.
        toggle = page.evaluate("""() => {
          const inst = () => [...chartInstances.values()][0];
          const dateAxis = () => ({
            price: !!inst().price.options.scales.x.ticks.display,
            pe:    !!inst().pe.options.scales.x.ticks.display,
            vol:   !!inst().vol.options.scales.x.ticks.display});
          const wrap = document.querySelector('.vol-wrap');
          const volChip = () => [...document.querySelectorAll('#panel-toggle .series-chip')]
                                   .find(c => c.textContent === 'Volume');
          const chip = volChip();
          const a11y = {role: chip.getAttribute('role'), pressed: chip.getAttribute('aria-pressed'),
                        tabindex: chip.getAttribute('tabindex')};
          const before = {wrap: getComputedStyle(wrap).display, axis: dateAxis()};
          volChip().click();   // togglePanel rebuilds chips + charts, so re-query afterwards
          return {a11y, before,
                  after: {wrap: getComputedStyle(wrap).display, axis: dateAxis(),
                          off: volChip().classList.contains('off')}};
        }""")
    finally:
        browser.close()
        pw.stop()

    assert set(diag) == {"AAA", "BBB"}, diag
    # price, P/E and volume panels must share one plot area (the alignment bug)
    for t in ("AAA", "BBB"):
        assert diag[t]["pe"] == diag[t]["vol"], f"{t}: volume x-axis misaligned with lines"
        assert diag[t]["price"] == diag[t]["pe"], f"{t}: price x-axis misaligned with lines"
    # loss shading: present for the loss-maker, absent for the clean name
    assert diag["AAA"]["bands"] >= 1, "loss-making ticker should be shaded"
    assert diag["BBB"]["bands"] == 0, "always-profitable ticker should have no shading"
    # panel toggle hides the volume panels and marks the chip off
    assert toggle["before"]["wrap"] != "none" and toggle["after"]["wrap"] == "none", toggle
    assert toggle["after"]["off"], "Volume chip should be struck-through after clicking"
    # date axis: volume owns it while visible; hiding volume re-homes labels to P/E
    assert toggle["before"]["axis"] == {"price": False, "pe": False, "vol": True}, toggle
    assert toggle["after"]["axis"]["pe"] is True, "hiding Volume must move date labels to P/E"
    assert toggle["after"]["axis"]["vol"] is False, toggle
    # toggle chips are keyboard-accessible buttons (aria-pressed reflects shown state)
    assert toggle["a11y"] == {"role": "button", "pressed": "true", "tabindex": "0"}, toggle["a11y"]
    assert not page_errors, f"chart raised JS errors: {page_errors}"
