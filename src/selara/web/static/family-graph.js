(() => {
  const dataScript = document.getElementById("family-graph-data");
  const scene = document.getElementById("family-scene");
  const inner = document.getElementById("family-scene-inner");
  const sideList = document.getElementById("family-side-list");
  if (!dataScript || !scene || !inner || !sideList) {
    return;
  }

  const { nodes, edges, focus_user_id: focusUserId } = JSON.parse(dataScript.textContent);

  const roleOrder = ["grandparent", "parent", "step_parent", "sibling", "subject", "spouse", "child", "pet", "relative"];
  const grouped = new Map();
  for (const role of roleOrder) grouped.set(role, []);
  for (const node of nodes) {
    const role = grouped.has(node.role) ? node.role : "relative";
    grouped.get(role).push(node);
  }

  const width = 980;
  const height = 620;
  inner.style.width = `${width}px`;
  inner.style.height = `${height}px`;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.classList.add("family-svg");
  inner.appendChild(svg);

  const positionMap = new Map();
  const placeRow = (items, y, startX, stepX) => {
    const centered = startX - ((Math.max(0, items.length - 1) * stepX) / 2);
    items.forEach((node, index) => {
      positionMap.set(node.id, { x: centered + index * stepX, y });
    });
  };

  placeRow(grouped.get("grandparent"), 86, width / 2, 180);
  placeRow([...grouped.get("parent"), ...grouped.get("step_parent")], 190, width / 2, 170);
  placeRow([...grouped.get("sibling")], 308, width / 2 - 260, 150);
  placeRow([...grouped.get("subject"), ...grouped.get("spouse")], 308, width / 2 + 90, 190);
  placeRow([...grouped.get("child")], 442, width / 2, 170);
  placeRow([...grouped.get("pet")], 540, width / 2, 150);
  placeRow([...grouped.get("relative")], 308, width / 2, 150);

  edges.forEach((edge) => {
    const source = positionMap.get(edge.source);
    const target = positionMap.get(edge.target);
    if (!source || !target) return;
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", String(source.x));
    line.setAttribute("y1", String(source.y));
    line.setAttribute("x2", String(target.x));
    line.setAttribute("y2", String(target.y));
    line.setAttribute("class", `family-edge edge-${edge.relation_type}`);
    if (!edge.is_direct) line.setAttribute("stroke-dasharray", "8 6");
    svg.appendChild(line);
  });

  const createStatRow = (label, detail, suffix) => {
    const row = document.createElement("div");
    row.className = "stat-row";
    const body = document.createElement("div");
    const heading = document.createElement("strong");
    heading.textContent = String(label);
    const description = document.createElement("p");
    description.textContent = String(detail);
    const marker = document.createElement("span");
    marker.textContent = String(suffix);
    body.append(heading, description);
    row.append(body, marker);
    return row;
  };

  const renderSideList = (nodeId) => {
    sideList.replaceChildren();
    const node = nodes.find((item) => item.id === nodeId);
    if (!node) return;

    sideList.appendChild(createStatRow(node.label, `роль: ${node.role}`, `#${node.id}`));

    const related = edges.filter((edge) => edge.source === nodeId || edge.target === nodeId);
    if (!related.length) {
      const empty = document.createElement("p");
      empty.className = "empty-text";
      empty.textContent = "Для этого узла связи не найдены.";
      sideList.appendChild(empty);
      return;
    }

    related.forEach((edge) => {
      const otherId = edge.source === nodeId ? edge.target : edge.source;
      const otherNode = nodes.find((item) => item.id === otherId);
      sideList.appendChild(createStatRow(otherNode?.label || otherId, edge.label, edge.relation_type));
    });
  };

  nodes.forEach((node) => {
    const pos = positionMap.get(node.id);
    if (!pos) return;
    const anchor = document.createElement("a");
    anchor.href = node.href;
    anchor.className = `family-node role-${node.role}`;
    anchor.style.left = `${pos.x}px`;
    anchor.style.top = `${pos.y}px`;
    const heading = document.createElement("strong");
    heading.textContent = String(node.label);
    const marker = document.createElement("span");
    marker.textContent = `#${node.id}`;
    anchor.append(heading, marker);
    anchor.addEventListener("mouseenter", () => renderSideList(node.id));
    anchor.addEventListener("focus", () => renderSideList(node.id));
    inner.appendChild(anchor);
  });

  renderSideList(focusUserId);

  const fitToContainer = () => {
    const scale = Math.min(1, scene.clientWidth / width);
    inner.style.transform = `scale(${scale})`;
    scene.style.height = `${height * scale}px`;
  };

  fitToContainer();
  window.addEventListener("resize", fitToContainer);
})();
