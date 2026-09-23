// Voice Agent Builder — vanilla JS, no build step. Talks to the FastAPI backend
// in server.py (same origin, so no CORS to worry about).

const NODE_TYPES = ["message", "collect", "decision", "tool_call", "end"];
const PROP_TYPES = ["string", "number", "boolean"];
const NODE_WIDTH = 220;

const state = {
  view: "list",
  agentId: null,
  config: null, // the full agent JSON, edited in place
  selectedNode: null,
  pan: { x: 60, y: 40 },
  zoom: 1,
  drag: null, // {mode: 'node'|'pan'|'connect', ...}
  pc: null,
  dirty: false,
};

const el = {};

document.addEventListener("DOMContentLoaded", () => {
  cacheEls();
  bindGlobalEvents();
  loadAgentList();
});

function cacheEls() {
  [
    "view-list", "view-editor", "agent-list", "template-select", "btn-new-agent",
    "editor-toolbar", "btn-back", "agent-name-input", "save-status", "btn-add-node", "btn-save",
    "canvas-wrap", "canvas-viewport", "canvas-content", "edge-layer", "validation-banner",
    "inspector", "inspector-empty", "inspector-form", "btn-call", "call-status", "remote-audio",
  ].forEach((id) => (el[toCamel(id)] = document.getElementById(id)));
}

function toCamel(id) {
  return id.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
}

function bindGlobalEvents() {
  el.btnNewAgent.addEventListener("click", onCreateAgent);
  el.btnBack.addEventListener("click", () => {
    if (state.dirty && !confirm("Discard unsaved changes?")) return;
    showList();
  });
  el.btnAddNode.addEventListener("click", onAddNode);
  el.btnSave.addEventListener("click", onSave);
  el.btnCall.addEventListener("click", onCallButton);
  el.agentNameInput.addEventListener("input", () => {
    state.config.name = el.agentNameInput.value;
    markDirty();
  });

  el.canvasViewport.addEventListener("mousedown", onViewportMouseDown);
  el.canvasViewport.addEventListener("wheel", onViewportWheel, { passive: false });
  window.addEventListener("mousemove", onWindowMouseMove);
  window.addEventListener("mouseup", onWindowMouseUp);
}

function api(path, opts) {
  return fetch(path, opts).then(async (res) => {
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const body = await res.json();
        detail = body.detail || JSON.stringify(body);
      } catch (_) {}
      throw new Error(detail);
    }
    if (res.status === 204) return null;
    return res.json();
  });
}

// ---------------------------------------------------------------- agent list

function loadAgentList() {
  Promise.all([api("/api/agents"), api("/api/templates")]).then(([agents, templates]) => {
    el.templateSelect.innerHTML = templates
      .map((t) => `<option value="${t.id}">${escapeHtml(t.name)}</option>`)
      .join("");
    renderAgentList(agents);
  });
}

function renderAgentList(agents) {
  if (!agents.length) {
    el.agentList.innerHTML = `<li class="empty">No agents yet — create one to get started.</li>`;
    return;
  }
  el.agentList.innerHTML = "";
  agents.forEach((a) => {
    const li = document.createElement("li");
    const updated = new Date(a.updated_at * 1000).toLocaleString();
    li.innerHTML = `
      <div>
        <div>${escapeHtml(a.name)}</div>
        <div class="agent-meta">updated ${updated}</div>
      </div>
      <div class="agent-actions">
        <button class="small-btn" data-action="delete">Delete</button>
      </div>`;
    li.addEventListener("click", (e) => {
      if (e.target.dataset.action === "delete") {
        e.stopPropagation();
        if (confirm(`Delete "${a.name}"? This can't be undone.`)) {
          api(`/api/agents/${a.id}`, { method: "DELETE" }).then(loadAgentList);
        }
        return;
      }
      openAgent(a.id);
    });
    el.agentList.appendChild(li);
  });
}

function onCreateAgent() {
  const template = el.templateSelect.value;
  api("/api/agents", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ template }),
  }).then(({ id }) => openAgent(id));
}

// ------------------------------------------------------------------ routing

function showList() {
  state.view = "list";
  state.agentId = null;
  state.config = null;
  endCall();
  el.viewEditor.classList.add("hidden");
  el.viewList.classList.remove("hidden");
  loadAgentList();
}

function openAgent(agentId) {
  api(`/api/agents/${agentId}`).then((config) => {
    state.view = "editor";
    state.agentId = agentId;
    state.config = config;
    state.selectedNode = null;
    state.dirty = false;
    state.pan = { x: 60, y: 40 };
    state.zoom = 1;
    el.viewList.classList.add("hidden");
    el.viewEditor.classList.remove("hidden");
    el.agentNameInput.value = config.name || "";
    setSaveStatus("");
    applyCanvasTransform();
    renderAll();
  });
}

// --------------------------------------------------------------- validation

function validateConfig(config) {
  const issues = [];
  const nodes = config.nodes || [];
  if (!nodes.length) {
    issues.push({ severity: "error", message: "Agent has no nodes." });
    return issues;
  }
  const names = nodes.map((n) => n.name);
  const nameSet = new Set(names);
  const seen = new Set();
  names.forEach((n) => {
    if (seen.has(n)) issues.push({ severity: "error", node: n, message: `Duplicate node name '${n}'.` });
    seen.add(n);
  });
  if (!nameSet.has(config.initial_node)) {
    issues.push({ severity: "error", message: `initial_node '${config.initial_node}' is not a defined node.` });
  }
  const byName = {};
  nodes.forEach((n) => (byName[n.name] = n));
  nodes.forEach((node) => {
    if (!node.task_messages || !node.task_messages.length) {
      issues.push({ severity: "warning", node: node.name, message: "Node has no task_messages." });
    }
    if ((!node.edges || !node.edges.length) && !node.end) {
      issues.push({
        severity: "warning",
        node: node.name,
        message: "Node has no outgoing edges and isn't marked as 'end' — the conversation could get stuck here.",
      });
    }
    (node.edges || []).forEach((edge) => {
      if (!nameSet.has(edge.target)) {
        issues.push({ severity: "error", node: node.name, message: `Edge '${edge.function}' targets unknown node '${edge.target}'.` });
      }
    });
  });
  if (nameSet.has(config.initial_node)) {
    const reachable = new Set([config.initial_node]);
    const stack = [config.initial_node];
    while (stack.length) {
      const cur = stack.pop();
      (byName[cur].edges || []).forEach((e) => {
        if (nameSet.has(e.target) && !reachable.has(e.target)) {
          reachable.add(e.target);
          stack.push(e.target);
        }
      });
    }
    nodes.forEach((n) => {
      if (!reachable.has(n.name)) {
        issues.push({ severity: "warning", node: n.name, message: "Node is unreachable from the initial node." });
      }
    });
  }
  return issues;
}

function renderValidation(issues) {
  const errors = issues.filter((i) => i.severity === "error");
  const warnings = issues.filter((i) => i.severity === "warning");
  if (!issues.length) {
    el.validationBanner.classList.add("hidden");
    return;
  }
  el.validationBanner.classList.remove("hidden");
  el.validationBanner.classList.toggle("has-error", errors.length > 0);
  const line = (i) =>
    `<li class="issue-${i.severity}">${i.node ? `<b>${escapeHtml(i.node)}</b>: ` : ""}${escapeHtml(i.message)}</li>`;
  el.validationBanner.innerHTML =
    `<div>${errors.length} error(s), ${warnings.length} warning(s)</div>` +
    `<ul>${issues.map(line).join("")}</ul>`;
}

function issuesByNode(issues) {
  const map = {};
  issues.forEach((i) => {
    if (!i.node) return;
    (map[i.node] = map[i.node] || []).push(i);
  });
  return map;
}

// -------------------------------------------------------------------- utils

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function uniqueNodeName(base) {
  let i = 1;
  const names = new Set(state.config.nodes.map((n) => n.name));
  let candidate = base;
  while (names.has(candidate)) candidate = `${base}_${i++}`;
  return candidate;
}

function markDirty() {
  state.dirty = true;
  setSaveStatus("Unsaved changes");
}

function setSaveStatus(text) {
  el.saveStatus.textContent = text;
}

function getNode(name) {
  return state.config.nodes.find((n) => n.name === name);
}

// ------------------------------------------------------------------ render

function renderAll() {
  renderCanvas();
  renderInspector();
}

function renderCanvas() {
  const issues = validateConfig(state.config);
  renderValidation(issues);
  const byNode = issuesByNode(issues);

  // Wipe and rebuild node elements (simplest correct approach for this scale of graph).
  Array.from(el.canvasContent.querySelectorAll(".node")).forEach((n) => n.remove());

  state.config.nodes.forEach((node) => {
    const div = document.createElement("div");
    const nodeIssues = byNode[node.name] || [];
    const hasError = nodeIssues.some((i) => i.severity === "error");
    const hasWarning = nodeIssues.some((i) => i.severity === "warning");
    div.className = `node type-${node.type}`;
    if (node.name === state.selectedNode) div.classList.add("selected");
    if (node.name === state.config.initial_node) div.classList.add("is-initial");
    if (hasError) div.classList.add("has-error");
    else if (hasWarning) div.classList.add("has-warning");
    div.dataset.name = node.name;
    const pos = node.position && "x" in node.position ? node.position : { x: 40, y: 40 };
    div.style.left = `${pos.x}px`;
    div.style.top = `${pos.y}px`;
    const preview = (node.task_messages && node.task_messages[0] && node.task_messages[0].content) || "";
    div.innerHTML = `
      <div class="node-header">
        <span class="node-type-badge">${node.type}</span>
        <span class="node-title">${escapeHtml(node.name)}</span>
      </div>
      <div class="node-body">${escapeHtml(preview)}</div>
      <div class="node-edge-count">${(node.edges || []).length} edge(s)</div>
      ${node.type !== "end" ? '<div class="node-port"></div>' : ""}
    `;
    div.addEventListener("mousedown", (e) => onNodeMouseDown(e, node));
    el.canvasContent.appendChild(div);
  });

  requestAnimationFrame(drawEdges);
}

function drawEdges() {
  const svg = el.edgeLayer;
  Array.from(svg.querySelectorAll(".edge-path, .edge-label")).forEach((n) => n.remove());
  ensureMarker();

  state.config.nodes.forEach((node) => {
    const sourceEl = el.canvasContent.querySelector(`.node[data-name="${cssEscape(node.name)}"]`);
    if (!sourceEl) return;
    (node.edges || []).forEach((edge) => {
      const targetEl = el.canvasContent.querySelector(`.node[data-name="${cssEscape(edge.target)}"]`);
      if (!targetEl) return;
      const sx = sourceEl.offsetLeft + sourceEl.offsetWidth;
      const sy = sourceEl.offsetTop + sourceEl.offsetHeight / 2;
      const tx = targetEl.offsetLeft;
      const ty = targetEl.offsetTop + targetEl.offsetHeight / 2;
      const dx = Math.max(40, (tx - sx) / 2);
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", `M ${sx} ${sy} C ${sx + dx} ${sy}, ${tx - dx} ${ty}, ${tx} ${ty}`);
      path.setAttribute("class", "edge-path");
      path.setAttribute("marker-end", "url(#arrow)");
      svg.appendChild(path);

      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.setAttribute("x", (sx + tx) / 2);
      label.setAttribute("y", (sy + ty) / 2 - 6);
      label.setAttribute("class", "edge-label");
      label.setAttribute("text-anchor", "middle");
      label.textContent = edge.function;
      svg.appendChild(label);
    });
  });
}

function ensureMarker() {
  if (el.edgeLayer.querySelector("#arrow")) return;
  const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
  defs.innerHTML = `
    <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
      <path d="M0,0 L8,4 L0,8 Z" fill="#9198ab" />
    </marker>`;
  el.edgeLayer.prepend(defs);
}

function cssEscape(s) {
  return window.CSS && CSS.escape ? CSS.escape(s) : s.replace(/"/g, '\\"');
}

function applyCanvasTransform() {
  el.canvasContent.style.transform = `translate(${state.pan.x}px, ${state.pan.y}px) scale(${state.zoom})`;
}

// --------------------------------------------------------- canvas: pan/zoom

function onViewportMouseDown(e) {
  if (e.target.closest(".node")) return; // handled by node handler
  state.drag = { mode: "pan", startX: e.clientX, startY: e.clientY, panStart: { ...state.pan } };
  el.canvasViewport.classList.add("panning");
}

function onViewportWheel(e) {
  e.preventDefault();
  const delta = e.deltaY > 0 ? -0.1 : 0.1;
  state.zoom = Math.min(2, Math.max(0.4, state.zoom + delta));
  applyCanvasTransform();
}

function toModelCoords(clientX, clientY) {
  const rect = el.canvasViewport.getBoundingClientRect();
  return {
    x: (clientX - rect.left - state.pan.x) / state.zoom,
    y: (clientY - rect.top - state.pan.y) / state.zoom,
  };
}

// --------------------------------------------------------------- node drag

function onNodeMouseDown(e, node) {
  if (e.target.classList.contains("node-port")) {
    e.stopPropagation();
    startConnect(node);
    return;
  }
  e.stopPropagation();
  selectNode(node.name);
  const start = toModelCoords(e.clientX, e.clientY);
  const pos = node.position && "x" in node.position ? node.position : { x: 40, y: 40 };
  state.drag = { mode: "node", node, offsetX: start.x - pos.x, offsetY: start.y - pos.y };
}

function startConnect(sourceNode) {
  const sourceEl = el.canvasContent.querySelector(`.node[data-name="${cssEscape(sourceNode.name)}"]`);
  const sx = sourceEl.offsetLeft + sourceEl.offsetWidth;
  const sy = sourceEl.offsetTop + sourceEl.offsetHeight / 2;
  const temp = document.createElementNS("http://www.w3.org/2000/svg", "path");
  temp.setAttribute("id", "temp-edge");
  temp.setAttribute("d", `M ${sx} ${sy} L ${sx} ${sy}`);
  el.edgeLayer.appendChild(temp);
  state.drag = { mode: "connect", source: sourceNode, sx, sy, temp };
}

function onWindowMouseMove(e) {
  const drag = state.drag;
  if (!drag) return;
  if (drag.mode === "pan") {
    state.pan = { x: drag.panStart.x + (e.clientX - drag.startX), y: drag.panStart.y + (e.clientY - drag.startY) };
    applyCanvasTransform();
  } else if (drag.mode === "node") {
    const m = toModelCoords(e.clientX, e.clientY);
    drag.node.position = { x: Math.round(m.x - drag.offsetX), y: Math.round(m.y - drag.offsetY) };
    const nodeEl = el.canvasContent.querySelector(`.node[data-name="${cssEscape(drag.node.name)}"]`);
    if (nodeEl) {
      nodeEl.style.left = `${drag.node.position.x}px`;
      nodeEl.style.top = `${drag.node.position.y}px`;
    }
    drawEdges();
  } else if (drag.mode === "connect") {
    const m = toModelCoords(e.clientX, e.clientY);
    drag.temp.setAttribute("d", `M ${drag.sx} ${drag.sy} L ${m.x} ${m.y}`);
  }
}

function onWindowMouseUp(e) {
  const drag = state.drag;
  if (!drag) return;
  if (drag.mode === "pan") {
    el.canvasViewport.classList.remove("panning");
  } else if (drag.mode === "node") {
    markDirty();
  } else if (drag.mode === "connect") {
    drag.temp.remove();
    const targetDiv = e.target.closest(".node");
    if (targetDiv && targetDiv.dataset.name !== drag.source.name) {
      addEdge(drag.source, targetDiv.dataset.name);
    }
  }
  state.drag = null;
}

// ------------------------------------------------------------- node/edges

function onAddNode() {
  const name = uniqueNodeName("new_node");
  const node = {
    name,
    type: "message",
    task_messages: [{ role: "developer", content: "" }],
    edges: [],
    end: false,
    data_refs: {},
    position: { x: -state.pan.x / state.zoom + 120, y: -state.pan.y / state.zoom + 120 },
  };
  state.config.nodes.push(node);
  markDirty();
  selectNode(name);
  renderCanvas();
}

function addEdge(sourceNode, targetName) {
  sourceNode.edges = sourceNode.edges || [];
  const fn = uniqueFunctionName(sourceNode, `go_to_${targetName}`);
  sourceNode.edges.push({
    function: fn,
    description: "",
    target: targetName,
    properties: {},
    required: [],
  });
  markDirty();
  selectNode(sourceNode.name);
  renderAll();
}

function uniqueFunctionName(node, base) {
  const names = new Set((node.edges || []).map((e) => e.function));
  let candidate = base;
  let i = 1;
  while (names.has(candidate)) candidate = `${base}_${i++}`;
  return candidate;
}

function selectNode(name) {
  state.selectedNode = name;
  document.querySelectorAll(".node").forEach((d) => d.classList.toggle("selected", d.dataset.name === name));
  renderInspector();
}

function renameNode(node, newName) {
  const oldName = node.name;
  if (!newName || newName === oldName) return;
  if (state.config.nodes.some((n) => n !== node && n.name === newName)) {
    alert(`A node named '${newName}' already exists.`);
    renderInspector();
    return;
  }
  node.name = newName;
  state.config.nodes.forEach((n) => (n.edges || []).forEach((e) => { if (e.target === oldName) e.target = newName; }));
  if (state.config.initial_node === oldName) state.config.initial_node = newName;
  state.selectedNode = newName;
  markDirty();
  renderAll();
}

function deleteNode(node) {
  if (node.name === state.config.initial_node) {
    alert("This is the start node — set a different node as start before deleting it.");
    return;
  }
  if (!confirm(`Delete node '${node.name}'? Edges pointing to it will be removed too.`)) return;
  state.config.nodes = state.config.nodes.filter((n) => n !== node);
  state.config.nodes.forEach((n) => {
    n.edges = (n.edges || []).filter((e) => e.target !== node.name);
  });
  state.selectedNode = null;
  markDirty();
  renderAll();
}

// ------------------------------------------------------------- inspector UI

function renderInspector() {
  const node = state.selectedNode ? getNode(state.selectedNode) : null;
  if (!node) {
    el.inspectorEmpty.classList.remove("hidden");
    el.inspectorForm.classList.add("hidden");
    return;
  }
  el.inspectorEmpty.classList.add("hidden");
  el.inspectorForm.classList.remove("hidden");

  const otherNodeNames = state.config.nodes.filter((n) => n !== node).map((n) => n.name);
  const isStart = node.name === state.config.initial_node;
  const taskContent = (node.task_messages && node.task_messages[0] && node.task_messages[0].content) || "";

  el.inspectorForm.innerHTML = `
    <div class="inspector-section">
      <label>Node name</label>
      <input id="f-name" type="text" value="${escapeHtml(node.name)}" />

      <label>Type</label>
      <select id="f-type">
        ${NODE_TYPES.map((t) => `<option value="${t}" ${t === node.type ? "selected" : ""}>${t}</option>`).join("")}
      </select>

      <label>What the agent says / does here</label>
      <textarea id="f-task" rows="3">${escapeHtml(taskContent)}</textarea>

      <div class="row" style="margin-top:10px;">
        <button id="f-set-start" class="small-btn" ${isStart ? "disabled" : ""}>${isStart ? "Start node" : "Set as start"}</button>
        <button id="f-delete" class="small-btn danger">Delete node</button>
      </div>
    </div>

    <div class="inspector-section">
      <h3>Data references <span class="field-hint">(reserved for Phase 2)</span></h3>
      <textarea id="f-data-refs" rows="2">${escapeHtml(JSON.stringify(node.data_refs || {}, null, 2))}</textarea>
    </div>

    ${node.type !== "end" ? `
    <div class="inspector-section">
      <h3>Outgoing edges</h3>
      <div id="edges-container"></div>
      <button id="f-add-edge" class="small-btn">+ Add edge</button>
    </div>` : ""}
  `;

  document.getElementById("f-name").addEventListener("change", (e) => renameNode(node, e.target.value.trim()));
  document.getElementById("f-type").addEventListener("change", (e) => {
    node.type = e.target.value;
    node.end = node.type === "end";
    if (node.end) node.edges = [];
    markDirty();
    renderAll();
  });
  document.getElementById("f-task").addEventListener("input", (e) => {
    node.task_messages = e.target.value ? [{ role: "developer", content: e.target.value }] : [];
    markDirty();
  });
  document.getElementById("f-set-start").addEventListener("click", () => {
    state.config.initial_node = node.name;
    markDirty();
    renderAll();
  });
  document.getElementById("f-delete").addEventListener("click", () => deleteNode(node));

  document.getElementById("f-data-refs").addEventListener("change", (e) => {
    try {
      node.data_refs = e.target.value.trim() ? JSON.parse(e.target.value) : {};
      markDirty();
    } catch (err) {
      alert("Data references must be valid JSON.");
      e.target.value = JSON.stringify(node.data_refs || {}, null, 2);
    }
  });

  const addEdgeBtn = document.getElementById("f-add-edge");
  if (addEdgeBtn) {
    addEdgeBtn.addEventListener("click", () => {
      if (!otherNodeNames.length) {
        alert("Add another node first.");
        return;
      }
      addEdge(node, otherNodeNames[0]);
    });
  }

  renderEdgeCards(node, otherNodeNames);
}

function renderEdgeCards(node, otherNodeNames) {
  const container = document.getElementById("edges-container");
  if (!container) return;
  container.innerHTML = "";
  (node.edges || []).forEach((edge, idx) => {
    const card = document.createElement("div");
    card.className = "edge-card";
    card.innerHTML = `
      <div class="row">
        <input class="e-function" type="text" value="${escapeHtml(edge.function)}" placeholder="function name" />
        <button class="icon-btn e-delete" title="Delete edge">✕</button>
      </div>
      <div class="row">
        <select class="e-target">
          ${otherNodeNames.map((n) => `<option value="${n}" ${n === edge.target ? "selected" : ""}>${escapeHtml(n)}</option>`).join("")}
        </select>
      </div>
      <div class="row">
        <textarea class="e-description" rows="2" placeholder="When should the model call this?">${escapeHtml(edge.description || "")}</textarea>
      </div>
      <div class="field-hint">Properties collected on this edge</div>
      <div class="props-container"></div>
      <button class="icon-btn e-add-prop small-btn">+ Property</button>
    `;
    card.querySelector(".e-function").addEventListener("change", (e) => { edge.function = e.target.value.trim(); markDirty(); drawEdges(); });
    card.querySelector(".e-target").addEventListener("change", (e) => { edge.target = e.target.value; markDirty(); renderAll(); });
    card.querySelector(".e-description").addEventListener("change", (e) => { edge.description = e.target.value; markDirty(); });
    card.querySelector(".e-delete").addEventListener("click", () => {
      node.edges.splice(idx, 1);
      markDirty();
      renderAll();
    });
    card.querySelector(".e-add-prop").addEventListener("click", () => {
      edge.properties = edge.properties || {};
      let name = "field";
      let i = 1;
      while (edge.properties[name]) name = `field_${i++}`;
      edge.properties[name] = { type: "string", description: "" };
      markDirty();
      renderEdgeCards(node, otherNodeNames);
    });
    renderProps(card.querySelector(".props-container"), edge);
    container.appendChild(card);
  });
}

function renderProps(container, edge) {
  container.innerHTML = "";
  const props = edge.properties || {};
  Object.keys(props).forEach((propName) => {
    const prop = props[propName];
    const row = document.createElement("div");
    row.className = "prop-row";
    row.innerHTML = `
      <input class="p-name" type="text" value="${escapeHtml(propName)}" placeholder="name" />
      <select class="p-type">${PROP_TYPES.map((t) => `<option value="${t}" ${t === prop.type ? "selected" : ""}>${t}</option>`).join("")}</select>
      <input class="p-desc" type="text" value="${escapeHtml(prop.description || "")}" placeholder="description" />
      <label style="display:flex;align-items:center;gap:4px;font-size:11px;">
        <input class="p-required" type="checkbox" ${(edge.required || []).includes(propName) ? "checked" : ""} /> req
      </label>
      <button class="icon-btn p-delete">✕</button>
    `;
    row.querySelector(".p-name").addEventListener("change", (e) => {
      const newName = e.target.value.trim();
      if (!newName || newName === propName) { e.target.value = propName; return; }
      if (props[newName]) { alert("Duplicate property name."); e.target.value = propName; return; }
      props[newName] = props[propName];
      delete props[propName];
      edge.required = (edge.required || []).map((r) => (r === propName ? newName : r));
      markDirty();
      renderProps(container, edge);
    });
    row.querySelector(".p-type").addEventListener("change", (e) => { prop.type = e.target.value; markDirty(); });
    row.querySelector(".p-desc").addEventListener("change", (e) => { prop.description = e.target.value; markDirty(); });
    row.querySelector(".p-required").addEventListener("change", (e) => {
      edge.required = edge.required || [];
      if (e.target.checked) {
        if (!edge.required.includes(propName)) edge.required.push(propName);
      } else {
        edge.required = edge.required.filter((r) => r !== propName);
      }
      markDirty();
    });
    row.querySelector(".p-delete").addEventListener("click", () => {
      delete props[propName];
      edge.required = (edge.required || []).filter((r) => r !== propName);
      markDirty();
      renderProps(container, edge);
    });
    container.appendChild(row);
  });
}

// ------------------------------------------------------------------- save

function onSave() {
  api(`/api/agents/${state.agentId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(state.config),
  })
    .then((res) => {
      state.dirty = false;
      setSaveStatus("Saved");
      if (res.warnings && res.warnings.length) {
        setSaveStatus(`Saved (${res.warnings.length} warning(s))`);
      }
    })
    .catch((err) => {
      setSaveStatus("");
      alert(`Couldn't save: ${err.message}`);
    });
}

// -------------------------------------------------------------- test call

function onCallButton() {
  if (state.pc) {
    endCall();
  } else {
    startCall();
  }
}

async function startCall() {
  const issues = validateConfig(state.config);
  const errors = issues.filter((i) => i.severity === "error");
  if (errors.length) {
    alert("Fix these errors before starting a test call:\n" + errors.map((e) => `- ${e.node ? e.node + ": " : ""}${e.message}`).join("\n"));
    return;
  }
  if (state.dirty && !confirm("You have unsaved changes — the test call will use the last saved version. Continue?")) {
    return;
  }

  setCallStatus("connecting", "Connecting…");
  try {
    const pc = new RTCPeerConnection({ iceServers: [{ urls: "stun:stun.l.google.com:19302" }] });
    state.pc = pc;
    pc.addTransceiver("audio", { direction: "sendrecv" });
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((t) => pc.addTrack(t, stream));
    pc.ontrack = (e) => { el.remoteAudio.srcObject = e.streams[0]; };
    pc.onconnectionstatechange = () => {
      if (pc.connectionState === "connected") setCallStatus("live", "Live");
      else if (["failed", "disconnected", "closed"].includes(pc.connectionState)) {
        setCallStatus("ended", "Call ended");
        cleanupCall();
      }
    };

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    await waitForIceGathering(pc, 3000);

    const res = await fetch("/api/offer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sdp: pc.localDescription.sdp,
        type: pc.localDescription.type,
        pc_id: null,
        restart_pc: false,
        request_data: { agent_id: state.agentId },
      }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${res.status}`);
    }
    const answer = await res.json();
    await pc.setRemoteDescription({ sdp: answer.sdp, type: answer.type });
    el.btnCall.textContent = "End test call";
  } catch (err) {
    console.error(err);
    setCallStatus("error", `Error: ${err.message}`);
    cleanupCall();
  }
}

function waitForIceGathering(pc, timeoutMs) {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const check = () => {
      if (pc.iceGatheringState === "complete") {
        pc.removeEventListener("icegatheringstatechange", check);
        resolve();
      }
    };
    pc.addEventListener("icegatheringstatechange", check);
    setTimeout(resolve, timeoutMs);
  });
}

function endCall() {
  if (state.pc) state.pc.close();
  cleanupCall();
  setCallStatus("idle", "Idle");
}

function cleanupCall() {
  state.pc = null;
  el.btnCall.textContent = "Start test call";
}

function setCallStatus(cls, text) {
  el.callStatus.className = cls;
  el.callStatus.textContent = text;
}
