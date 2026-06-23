// Per-browser ag-grid column preferences, shared by the dashboard and Δ grids.
// Persists the *hidden* column set (so columns added later default to shown) and,
// when `widthsKey` is given, column widths. Order and sort are not persisted.
(function (global) {
  function load(key) {
    try { return JSON.parse(localStorage.getItem(key)) || null; }
    catch { return null; }
  }
  function save(key, val) { localStorage.setItem(key, JSON.stringify(val)); }

  function initChooser(btn, menu) {
    if (!btn || !menu) return;
    btn.addEventListener("click", e => {
      e.stopPropagation();
      const open = menu.classList.toggle("open");
      btn.setAttribute("aria-expanded", String(open));
    });
    menu.addEventListener("click", e => e.stopPropagation());
    document.addEventListener("click", () => {
      menu.classList.remove("open");
      btn.setAttribute("aria-expanded", "false");
    });
  }

  // Apply saved prefs, build the chooser menu, wire it, and return a handle whose
  // onColumnResized should be passed to the grid's onColumnResized option.
  function create({gridApi, columnDefs, hiddenKey, widthsKey, locked = [],
                   chooserBtn, chooserMenu}) {
    const lockedSet = new Set(locked);
    const loadHidden = () => new Set(load(hiddenKey) || []);
    const saveHidden = set => save(hiddenKey, [...set]);

    function applyVisibility() {
      const hidden = loadHidden();
      for (const def of columnDefs) {
        gridApi.setColumnsVisible(
          [def.field], lockedSet.has(def.field) || !hidden.has(def.field));
      }
    }

    function buildMenu() {
      if (!chooserMenu) return;
      const hidden = loadHidden();
      chooserMenu.innerHTML = "";
      for (const def of columnDefs) {
        const isLocked = lockedSet.has(def.field);
        const label = document.createElement("label");
        if (isLocked) label.classList.add("locked");
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = isLocked || !hidden.has(def.field);
        cb.disabled = isLocked;
        cb.addEventListener("change", () => {
          const h = loadHidden();
          if (cb.checked) h.delete(def.field); else h.add(def.field);
          saveHidden(h);
          gridApi.setColumnsVisible([def.field], cb.checked);
        });
        label.append(cb, document.createTextNode(def.headerName));
        chooserMenu.appendChild(label);
      }
    }

    function saveWidths() {
      if (!widthsKey) return;
      save(widthsKey, gridApi.getColumnState().map(
        s => ({colId: s.colId, width: s.width, flex: s.flex})));
    }
    function applyWidths() {
      if (!widthsKey) return;
      const saved = load(widthsKey);
      if (saved) gridApi.applyColumnState({state: saved, applyOrder: false});
    }

    applyVisibility();
    applyWidths();
    buildMenu();
    initChooser(chooserBtn, chooserMenu);

    return {
      onColumnResized: e => { if (e.finished && e.source === "uiColumnResized") saveWidths(); },
    };
  }

  global.GridPrefs = {create};
})(window);
