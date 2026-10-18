// Mandato · En vivo: anima la red de decisión con los eventos SSE del servidor.
(() => {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.getElementById("red");
  const capas = { aristas: svg.querySelector("#aristas"), nodos: svg.querySelector("#nodos"), pulsos: svg.querySelector("#pulsos") };
  const quieto = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const COLOR = { OK: "#1f9a62", ESCALAR: "#d9922b", FRENAR: "#d4483b", PAGAR: "#1f9a62", acento: "#2a4bd0" };
  const CANALES = [["pdf", "PDF"], ["imagen", "Imagen"], ["xml", "XML"], ["correo", "Correo"], ["chat", "Chat"]];
  const CONTROLES = ["R00","R01","R02","R03","R04","R05","R06","R07","R08","R09","R10","R11","R12","R13","J01","J02","J03","J04"];
  const SALIDAS = [["PAGAR", "Pagada"], ["ESCALAR", "Por revisar"], ["FRENAR", "Frenada"]];
  const nodos = {};

  const el = (tag, attrs, padre) => { const n = document.createElementNS(NS, tag); for (const k in attrs) n.setAttribute(k, attrs[k]); padre && padre.appendChild(n); return n; };
  const nodo = (id, x, y, r, etiqueta, lado) => {
    const g = el("g", { class: "nodo", "data-id": id, id }, capas.nodos);
    const c = el("circle", { cx: x, cy: y, r, class: "nodo__circulo" }, g);
    const t = el("text", { x: lado === "dentro" ? x : lado === "izq" ? x - r - 10 : x + r + 10, y: y + 4, class: "nodo__texto nodo__texto--" + lado,
      "text-anchor": lado === "dentro" ? "middle" : lado === "izq" ? "end" : "start" }, g);
    t.textContent = etiqueta;
    nodos[id] = { x, y, r, g, c };
  };
  const arista = (a, b) => el("line", { x1: nodos[a].x, y1: nodos[a].y, x2: nodos[b].x, y2: nodos[b].y, class: "arista" }, capas.aristas);

  // Capas: entradas → lecturas → 18 controles → salidas
  CANALES.forEach(([id, txt], i) => nodo("in-" + id, 105, 90 + i * 85, 18, txt, "izq"));
  nodo("lec-a", 320, 190, 24, "Lectura A", "der"); nodo("lec-b", 320, 330, 24, "Lectura B", "der");
  CONTROLES.forEach((id, i) => nodo("c-" + id, i < 9 ? 560 : 660, 50 + (i % 9) * 52, 20, id, "dentro"));
  SALIDAS.forEach(([id, txt], i) => nodo("out-" + id, 860, 150 + i * 110, 26, txt, "der"));
  CANALES.forEach(([id]) => { arista("in-" + id, "lec-a"); arista("in-" + id, "lec-b"); });
  CONTROLES.forEach((id) => { arista("lec-a", "c-" + id); arista("lec-b", "c-" + id); SALIDAS.forEach(([s]) => arista("c-" + id, "out-" + s)); });
  // las etiquetas de lectura van debajo para no chocar con las aristas
  ["lec-a", "lec-b"].forEach((id) => { const t = nodos[id].g.querySelector("text"); t.setAttribute("x", nodos[id].x); t.setAttribute("y", nodos[id].y + 48); t.setAttribute("text-anchor", "middle"); });

  const pulso = (a, b, color, dur = 0.38) => {
    if (quieto) return;
    const p = el("circle", { cx: nodos[a].x, cy: nodos[a].y, r: 4.5, fill: color, class: "pulso" }, capas.pulsos);
    gsap.to(p, { attr: { cx: nodos[b].x, cy: nodos[b].y }, duration: dur, ease: "power2.inOut", onComplete: () => p.remove() });
  };
  const encender = (id, color, dur = 0.25) => gsap.to(nodos[id].c, { fill: color, stroke: color, duration: quieto ? 0 : dur, ease: "power2.out" });
  const latido = (id) => quieto || gsap.fromTo(nodos[id].c, { attr: { r: nodos[id].r } }, { attr: { r: nodos[id].r * 1.35 }, duration: 0.18, yoyo: true, repeat: 1, ease: "power2.out" });
  const apagar = (ids) => gsap.to(ids.map((id) => nodos[id].c), { fill: "#ffffff", stroke: "rgba(28,37,58,.22)", duration: quieto ? 0 : 0.2 });

  // Estado
  const $ = (id) => document.getElementById(id);
  const cont = { PAGAR: 0, ESCALAR: 0, FRENAR: 0 };
  let total = 0, decididas = 0, leyendo = 0, t0 = 0, fuente = null;
  const cola = []; let animando = false;
  const formato = (v) => v == null ? "—" : new Intl.NumberFormat("es-CO", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v);
  const NOMBRE = { PAGAR: "Pagada", ESCALAR: "Por revisar", FRENAR: "Frenada" };

  function progreso() {
    $("progreso").textContent = total ? `${decididas} de ${total}` : "";
    $("leyendo").textContent = leyendo > 0 ? `${leyendo} en lectura` : "";
    if (decididas && t0) {
      const s = (performance.now() - t0) / 1000;
      $("ritmo").textContent = `${(decididas / s * 60).toFixed(0)} facturas/min`;
    }
  }

  function alRecibir(ev) {
    const canal = nodos["in-" + ev.canal] ? "in-" + ev.canal : "in-pdf";
    leyendo++; progreso();
    encender(canal, COLOR.acento, 0.15); latido(canal);
    pulso(canal, "lec-a", COLOR.acento); pulso(canal, "lec-b", COLOR.acento);
    gsap.delayedCall(0.6, () => apagar([canal]));
  }
  function alLeer(ev) {
    leyendo = Math.max(0, leyendo - 1); progreso();
    const c = ev.consistente ? COLOR.acento : COLOR.ESCALAR;
    encender("lec-a", c, 0.15); encender("lec-b", c, 0.15);
    gsap.delayedCall(0.35, () => apagar(["lec-a", "lec-b"]));
  }

  function animarDecision(ev) {
    return new Promise((listo) => {
      const rapido = 1 + Math.min(cola.length, 6) * 0.45;  // si la cola crece, la animación acelera
      const tl = gsap.timeline({ onComplete: listo }).timeScale(quieto ? 20 : rapido);
      const ids = CONTROLES.map((c) => "c-" + c);
      tl.add(() => apagar([...ids, ...SALIDAS.map(([s]) => "out-" + s)]));
      ev.reglas.forEach((r, k) => {
        tl.add(() => { pulso(k % 2 ? "lec-b" : "lec-a", "c-" + r.id, COLOR.acento, 0.22); }, k * 0.028);
        tl.add(() => encender("c-" + r.id, COLOR[r.v] || COLOR.OK, 0.16), 0.2 + k * 0.028);
      });
      const fin = 0.2 + ev.reglas.length * 0.028 + 0.15;
      tl.add(() => {
        const fallidas = ev.reglas.filter((r) => r.v !== "OK");
        (fallidas.length ? fallidas : ev.reglas.filter((_, k) => k % 3 === 0)).forEach((r) => pulso("c-" + r.id, "out-" + ev.decision, COLOR[ev.decision], 0.3));
      }, fin);
      tl.add(() => { encender("out-" + ev.decision, COLOR[ev.decision], 0.2); latido("out-" + ev.decision); mostrar(ev); }, fin + 0.3);
      tl.to({}, { duration: 0.35 });
    });
  }

  async function drenar() {
    if (animando) return; animando = true;
    while (cola.length) await animarDecision(cola.shift());
    animando = false;
  }

  function mostrar(ev) {
    cont[ev.decision]++; decididas++; progreso();
    $("c-pagar").textContent = cont.PAGAR; $("c-escalar").textContent = cont.ESCALAR; $("c-frenar").textContent = cont.FRENAR;
    $("actual-vacio").hidden = true; $("actual").hidden = false;
    $("actual-prov").textContent = `${ev.proveedor || "Documento sin proveedor legible"} ${ev.numero || ""}`;
    $("actual-monto").textContent = ev.monto == null ? "—" : `${ev.moneda || ""} ${formato(ev.monto)}`;
    $("actual-estado").innerHTML = "";
    const est = document.createElement("span");
    est.className = "estado estado--" + ev.decision.toLowerCase();
    est.textContent = NOMBRE[ev.decision] + (ev.regla ? ` · ${ev.regla}` : " · 18 controles en orden");
    $("actual-estado").appendChild(est);
    $("actual-expl").textContent = (ev.explicacion || "").replace(/^(PAGAR|ESCALAR|FRENAR) · /, "");
    $("actual-enlace").href = "/decisiones/" + ev.id;
    const li = document.createElement("li");
    li.innerHTML = `<a class="mov" href="/decisiones/${ev.id}"><span class="mov__chip mov__chip--${ev.decision.toLowerCase()}"></span><span class="mov__texto"><span class="mov__titulo"></span><span class="mov__sub"></span></span><span class="mov__monto"></span></a>`;
    li.querySelector(".mov__titulo").textContent = ev.proveedor || "Documento sin proveedor legible";
    li.querySelector(".mov__sub").textContent = NOMBRE[ev.decision] + (ev.regla ? ` · ${ev.regla}` : "");
    li.querySelector(".mov__monto").textContent = ev.monto == null ? "—" : `${ev.moneda || ""} ${formato(ev.monto)}`;
    const lista = $("lista"); lista.prepend(li);
    while (lista.children.length > 8) lista.lastChild.remove();
    quieto || gsap.from(li, { y: -8, autoAlpha: 0, duration: 0.25, ease: "power2.out" });
  }

  function reiniciar(modo) {
    fuente && fuente.close();
    cont.PAGAR = cont.ESCALAR = cont.FRENAR = 0; total = decididas = leyendo = 0; t0 = performance.now(); cola.length = 0;
    ["c-pagar", "c-escalar", "c-frenar"].forEach((id) => ($(id).textContent = "0"));
    $("lista").innerHTML = ""; $("ritmo").textContent = ""; $("modo").textContent = modo;
  }

  function escuchar(url, modo) {
    reiniciar(modo);
    fuente = new EventSource(url);
    fuente.onmessage = (m) => {
      const ev = JSON.parse(m.data);
      if (ev.tipo === "inicio") { total = ev.total; progreso(); }
      else if (ev.tipo === "recibida") alRecibir(ev);
      else if (ev.tipo === "leida") alLeer(ev);
      else if (ev.tipo === "decidida") { cola.push(ev); drenar(); }
      else if (ev.tipo === "error") { decididas++; leyendo = Math.max(0, leyendo - 1); progreso(); }
      else if (ev.tipo === "fin") {
        fuente.close(); $("modo").textContent = modo === "Repitiendo el registro" ? "Repetición terminada" : "Terminado";
        if (modo !== "Repitiendo el registro") setTimeout(() => location.assign("/"), 2500);  // resumen y registro al día
      }
    };
    fuente.onerror = () => { $("modo").textContent = "Conexión cerrada"; fuente.close(); };
  }

  $("repetir").addEventListener("click", () => escuchar("/api/repeticion", "Repitiendo el registro"));
  $("subir").addEventListener("submit", async (e) => {
    if (e.submitter && e.submitter.id === "v-demo") return;  // el set va por el formulario normal a /bandeja/demo
    e.preventDefault();
    const err = $("v-error"); err.hidden = true;
    if (!$("v-archivo").files.length) { err.textContent = "Elige al menos una factura."; err.hidden = false; return; }
    const r = await fetch("/api/trabajos", { method: "POST", body: new FormData(e.target) });
    if (!r.ok) { err.textContent = [403, 429].includes(r.status) ? (await r.json()).detail : "No se pudo subir: revisa el formato (PDF, PNG, JPG, XML, EML o TXT, hasta 5 MB cada una y 50 por vez)."; err.hidden = false; return; }
    const { trabajo } = await r.json();
    escuchar(`/api/trabajos/${trabajo}/eventos`, "Procesando en vivo");
  });
  const vivo = document.querySelector(".vivo");
  const inicial = vivo.dataset.trabajo;
  if (inicial) escuchar(`/api/trabajos/${inicial}/eventos`, "Procesando en vivo");
  else if (Number(vivo.dataset.registro) > 0 && !quieto) {  // lo primero que se ve: el registro animándose, una vez por sesión
    let visto = false;
    try { visto = sessionStorage.getItem("mandato-repeticion") === "1"; sessionStorage.setItem("mandato-repeticion", "1"); } catch (_) { /* sin almacenamiento: se repite */ }
    if (!visto) setTimeout(() => escuchar("/api/repeticion", "Repitiendo el registro"), 700);
  }
})();
