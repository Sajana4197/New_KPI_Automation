/* Background of the signed-in pages: a slowly moving transmission network.
   Dots are sites, lines are the links between them, and small coloured packets travel along
   the links in the three KPI colours (packet drop, jitter, RTT). Purely decorative. */
(function () {
  "use strict";
  var canvas = document.getElementById("bg-net");
  if (!canvas || !canvas.getContext) return;
  var ctx = canvas.getContext("2d");
  var still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var KPI = ["42,120,214", "235,104,52", "27,175,122"];      // packet drop, jitter, RTT
  var LINK = 170;                                             // longest link drawn, in px
  /* The page can ask for a busier network: data-density (more sites) and data-speed (faster drift and packets). */
  var DENSITY = Math.max(0.5, Math.min(3, +canvas.dataset.density || 1));
  var SPEED = Math.max(0.5, Math.min(3, +canvas.dataset.speed || 1));
  var fitParent = canvas.dataset.fit === "parent";     /* the sign-in page draws it in one panel only */
  var w = 0, h = 0, nodes = [], packets = [], rings = [], last = 0, running = false;

  function rand(a, b) { return a + Math.random() * (b - a); }

  function build() {
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    var box = fitParent ? canvas.parentElement.getBoundingClientRect() : null;
    w = box ? Math.round(box.width) : window.innerWidth; h = box ? Math.round(box.height) : window.innerHeight;
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    var want = Math.max(18, Math.min(140, Math.round(w * h / 17000 * DENSITY)));
    nodes = [];
    for (var i = 0; i < want; i++) {
      nodes.push({ x: rand(0, w), y: rand(0, h), vx: rand(-9, 9) * SPEED, vy: rand(-9, 9) * SPEED, r: rand(1.4, 2.6), hub: Math.random() < 0.12 });
    }
    packets = []; rings = [];
  }

  function neighbours(a) {
    var out = [];
    for (var i = 0; i < nodes.length; i++) {
      var b = nodes[i];
      if (b !== a && Math.abs(a.x - b.x) < LINK && Math.abs(a.y - b.y) < LINK && Math.hypot(a.x - b.x, a.y - b.y) < LINK) out.push(b);
    }
    return out;
  }

  function launch() {
    var a = nodes[Math.floor(Math.random() * nodes.length)], near = neighbours(a);
    if (!near.length) return;
    packets.push({ a: a, b: near[Math.floor(Math.random() * near.length)], t: 0, speed: rand(0.35, 0.8) * SPEED, hops: Math.floor(rand(1, 4)),
                   color: KPI[Math.floor(Math.random() * KPI.length)] });
  }

  function step(dt) {
    var i, n;
    for (i = 0; i < nodes.length; i++) {
      n = nodes[i];
      n.x += n.vx * dt; n.y += n.vy * dt;
      if (n.x < -20) n.x = w + 20; else if (n.x > w + 20) n.x = -20;
      if (n.y < -20) n.y = h + 20; else if (n.y > h + 20) n.y = -20;
    }
    if (packets.length < Math.max(4, nodes.length / 5 * SPEED) && Math.random() < dt * 5 * SPEED) launch();
    for (i = packets.length - 1; i >= 0; i--) {
      var p = packets[i];
      p.t += p.speed * dt;
      if (p.t < 1) continue;
      rings.push({ n: p.b, t: 0, color: p.color });
      var near = p.hops > 0 ? neighbours(p.b).filter(function (x) { return x !== p.a; }) : [];
      if (near.length) { p.a = p.b; p.b = near[Math.floor(Math.random() * near.length)]; p.t = 0; p.hops--; }
      else packets.splice(i, 1);
    }
    for (i = rings.length - 1; i >= 0; i--) { rings[i].t += dt * 1.1; if (rings[i].t >= 1) rings.splice(i, 1); }
  }

  function draw() {
    var i, j, a, b, d;
    ctx.clearRect(0, 0, w, h);
    ctx.lineWidth = 1;
    for (i = 0; i < nodes.length; i++) {
      a = nodes[i];
      for (j = i + 1; j < nodes.length; j++) {
        b = nodes[j];
        if (Math.abs(a.x - b.x) > LINK || Math.abs(a.y - b.y) > LINK) continue;
        d = Math.hypot(a.x - b.x, a.y - b.y);
        if (d > LINK) continue;
        ctx.strokeStyle = "rgba(0,86,162," + (0.26 * (1 - d / LINK)).toFixed(3) + ")";
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
      }
    }
    for (i = 0; i < nodes.length; i++) {
      a = nodes[i];
      ctx.fillStyle = a.hub ? "rgba(0,180,235,.55)" : "rgba(0,86,162,.42)";
      ctx.beginPath(); ctx.arc(a.x, a.y, a.hub ? a.r + 1.2 : a.r, 0, 6.2832); ctx.fill();
      if (a.hub) {
        ctx.strokeStyle = "rgba(0,180,235,.22)";
        ctx.beginPath(); ctx.arc(a.x, a.y, a.r + 5.5, 0, 6.2832); ctx.stroke();
      }
    }
    for (i = 0; i < rings.length; i++) {
      var r = rings[i];
      ctx.strokeStyle = "rgba(" + r.color + "," + (0.5 * (1 - r.t)).toFixed(3) + ")";
      ctx.lineWidth = 1.4;
      ctx.beginPath(); ctx.arc(r.n.x, r.n.y, 3 + r.t * 16, 0, 6.2832); ctx.stroke();
    }
    for (i = 0; i < packets.length; i++) {
      var p = packets[i], x = p.a.x + (p.b.x - p.a.x) * p.t, y = p.a.y + (p.b.y - p.a.y) * p.t;
      var tx = p.a.x + (p.b.x - p.a.x) * Math.max(0, p.t - 0.16), ty = p.a.y + (p.b.y - p.a.y) * Math.max(0, p.t - 0.16);
      var g = ctx.createLinearGradient(tx, ty, x, y);
      g.addColorStop(0, "rgba(" + p.color + ",0)"); g.addColorStop(1, "rgba(" + p.color + ",.75)");
      ctx.strokeStyle = g; ctx.lineWidth = 2; ctx.lineCap = "round";
      ctx.beginPath(); ctx.moveTo(tx, ty); ctx.lineTo(x, y); ctx.stroke();
      ctx.fillStyle = "rgba(" + p.color + ",.22)";
      ctx.beginPath(); ctx.arc(x, y, 6, 0, 6.2832); ctx.fill();
      ctx.fillStyle = "rgba(" + p.color + ",.95)";
      ctx.beginPath(); ctx.arc(x, y, 2.4, 0, 6.2832); ctx.fill();
    }
  }

  function frame(now) {
    if (!running) return;
    var dt = Math.min((now - last) / 1000, 0.05);
    last = now;
    step(dt); draw();
    window.requestAnimationFrame(frame);
  }

  function start() {
    if (running || still || document.hidden) return;
    running = true; last = performance.now();
    window.requestAnimationFrame(frame);
  }

  var timer = null;
  window.addEventListener("resize", function () {
    window.clearTimeout(timer);
    timer = window.setTimeout(function () {
      var box = fitParent ? canvas.parentElement.getBoundingClientRect() : { width: window.innerWidth, height: window.innerHeight };
      if (Math.round(box.width) === w && Math.round(box.height) === h) return;
      build(); draw();
    }, 150);
  });
  document.addEventListener("visibilitychange", function () { if (document.hidden) running = false; else start(); });

  build();
  draw();          // one still picture for people who asked their device for less motion
  start();
})();
