/* awm-ui-motion engine: every frame is a pure function of t (ms), so the renderer can seek it exactly.
   Load before the page script; everything lives on the global M. */
const M = (() => {
  const NS = 'http://www.w3.org/2000/svg';
  const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
  const lerp = (a, b, k) => a + (b - a) * k;
  const seg = (t, a, b) => clamp((t - a) / (b - a));
  const ease = {
    inOut: k => k < .5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2,
    inOut5: k => k < .5 ? 16 * k ** 5 : 1 - Math.pow(-2 * k + 2, 5) / 2,
    out: k => 1 - Math.pow(1 - k, 3),
    in: k => k * k * k,
  };
  const f3 = v => (+v).toFixed(3);
  const hex = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const mix = (a, b, k) => { const A = hex(a), B = hex(b); return 'rgb(' + A.map((v, i) => Math.round(lerp(v, B[i], k))).join(',') + ')'; };
  const hash = n => { const x = Math.sin(n * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };

  // 0 before a, fades in over fin, holds, fades out over fout ending at b
  const win = (t, a, b, fin = 300, fout = 300) => seg(t, a, a + fin) * (1 - seg(t, b - fout, b));

  // ---------- camera ----------
  // a view is the world rect origin (x, y) shown at scale s. A move zooms about the one point both views
  // share and interpolates scale in log space, so nothing slides sideways and zoom speed feels even.
  function between(a, b, e) {
    if (Math.abs(b.s - a.s) < 1e-6) return { x: lerp(a.x, b.x, e), y: lerp(a.y, b.y, e), s: a.s };
    const s = Math.exp(lerp(Math.log(a.s), Math.log(b.s), e));
    const fx = (b.x * b.s - a.x * a.s) / (b.s - a.s), fy = (b.y * b.s - a.y * a.s) / (b.s - a.s);
    return { x: fx - (fx - a.x) * a.s / s, y: fy - (fy - a.y) * a.s / s, s };
  }
  // views: {name: {x, y, s}}; moves: [[t0, t1, from, to], ...] sorted by t0. Returns cam(t).
  function camera(views, moves, curve = ease.inOut) {
    return t => {
      let v = views[moves.length ? moves[0][2] : Object.keys(views)[0]];
      for (const [a, b, i, j] of moves) {
        if (t < a) break;
        v = t >= b ? views[j] : between(views[i], views[j], curve(seg(t, a, b)));
      }
      return v;
    };
  }
  // a view that shows world point (px, py) at screen point (sx, sy) with scale s
  const focus = (px, py, s, sx, sy) => ({ x: px - sx / s, y: py - sy / s, s });
  // the view that shows world rect (x0, y0)-(x1, y1) whole, centred, with margin screen px around it
  function fit(x0, y0, x1, y1, W, H, margin = 80) {
    const s = Math.min((W - 2 * margin) / (x1 - x0), (H - 2 * margin) / (y1 - y0));
    return { x: (x0 + x1) / 2 - W / 2 / s, y: (y0 + y1) / 2 - H / 2 / s, s };
  }
  const setView = (svg, cam, W, H) => svg.setAttribute('viewBox', [cam.x, cam.y, W / cam.s, H / cam.s].map(f3).join(' '));
  const toScreen = (cam, x, y) => [(x - cam.x) * cam.s, (y - cam.y) * cam.s];

  // ---------- speech and typing ----------
  // syllable-timed words between t0 and t1; returns {words: [{w, at}], syllables: [{a, d, amp, hi}]}
  function speech(text, t0, t1, syllables) {
    const words = text.split(' ');
    const syl = syllables || words.map(w => Math.max(1, (w.toLowerCase().match(/[aeiouy]+/g) || []).length));
    let u = 0, n = 0;
    const raw = [], spans = [];
    words.forEach((w, i) => {
      const start = u;
      for (let s = 0; s < syl[i]; s++) {
        const dur = .13 + .07 * hash(n);
        raw.push({ a: u, dur, amp: .6 + .4 * hash(n + 50), hi: hash(n + 90) });
        u += dur; n++;
      }
      spans.push([start, u]);
      u += .05 + (/[.?!]$/.test(w) ? .42 : /,$/.test(w) ? .2 : 0);
    });
    const f = (t1 - t0) / (u * 1000);
    return {
      words: words.map((w, i) => ({ w, at: t0 + (spans[i][0] + .7 * (spans[i][1] - spans[i][0])) * 1000 * f })),
      syllables: raw.map(s => ({ a: t0 + s.a * 1000 * f, d: s.dur * 1000 * f * 1.15, amp: s.amp, hi: s.hi })),
    };
  }
  // a word fades in over 220 ms with a 2 px rise, never popping in
  const wordIn = (t, at) => { const k = ease.out(seg(t, at - 110, at + 110)); return { o: k, dy: 2 * (1 - k) }; };

  // a voice level baked once with an app-like meter filter (rise .55, fall .18 per 16 ms), read with interpolation
  function voice(syllables, t0, t1, step = 16) {
    const L = [], Hh = [], start = t0 - 400, end = t1 + 900;
    let l = 0, h = 0;
    for (let t = start; t <= end; t += step) {
      let rl = 0, rh = 0;
      for (const s of syllables) {
        const x = (t - s.a) / s.d;
        if (x <= 0 || x >= 1) continue;
        const b = s.amp * Math.pow(Math.sin(Math.PI * x), .8);
        rl += b; rh += b * s.hi;
      }
      const on = seg(t, t0 - 150, t0) * (1 - seg(t, t1, t1 + 250));
      rl = clamp(rl * .95 + on * (.04 + .03 * Math.sin(t * .0131) * Math.sin(t * .0057)));
      rh = clamp(rh * .8);
      l += (rl - l) * (rl > l ? .55 : .18);
      h += (rh - h) * (rh > h ? .55 : .18);
      L.push(l); Hh.push(h);
    }
    return t => {
      const p = (t - start) / step;
      if (p <= 0) return { level: 0, high: 0 };
      const i = Math.min(L.length - 2, Math.floor(p)), k = clamp(p - i);
      return { level: lerp(L[i], L[i + 1], k), high: lerp(Hh[i], Hh[i + 1], k) };
    };
  }

  // the brand wave: 120 points, pinched ends, two travelling sines driven by level and high
  function wave(t, level, high, x0 = 8, x1 = 64, mid = 14, n = 120) {
    const pts = [], ts = t / 1000;
    for (let i = 0; i <= n; i++) {
      const u = i / n, env = Math.pow(Math.max(0, Math.sin(Math.PI * u)), 1.4), a = (2 + 9 * level) * env;
      pts.push([x0 + (x1 - x0) * u,
        mid - a * (.6 * Math.sin(u * Math.PI * 4 - ts * 7) + .4 * Math.sin(u * Math.PI * 7 + ts * 4.3) * (.4 + high))]);
    }
    return pts;
  }
  const path = (pts, ox = 0, oy = 0) => pts.length ? 'M' + pts.map(p => f3(p[0] + ox) + ' ' + f3(p[1] + oy)).join('L') : '';

  // ---------- pointer ----------
  // keys: [{t, x, y}]; moves ease in and out with a slight bow, like a hand
  function pointer(keys) {
    return t => {
      if (t <= keys[0].t) return keys[0];
      for (let i = 1; i < keys.length; i++) {
        const a = keys[i - 1], b = keys[i];
        if (t <= b.t) {
          const e = ease.inOut(seg(t, a.t, b.t)), dx = b.x - a.x, dy = b.y - a.y, bow = .08 * Math.sin(Math.PI * e);
          return { x: a.x + dx * e - dy * bow, y: a.y + dy * e + dx * bow };
        }
      }
      return keys[keys.length - 1];
    };
  }
  // press dip (0..1) and ring progress (0..1) for the latest click at or before t
  function click(t, times, dur = 450) {
    let last = -1e9;
    for (const c of times) if (c <= t) last = c;
    const k = (t - last) / dur;
    return { press: k >= 0 && k < .35 ? Math.sin(Math.PI * k / .35) : 0, ring: k >= 0 && k < 1 ? k : 0 };
  }

  // ---------- svg building ----------
  function el(tag, attrs, parent, text) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (text != null) e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
  }
  const set = (e, attrs) => { for (const k in attrs) e.setAttribute(k, typeof attrs[k] === 'number' ? f3(attrs[k]) : attrs[k]); };

  // lays words out left to right, wrapping at maxW; fonts must be loaded (boot's setup runs after they are)
  function layoutWords(parent, words, x, y, maxW, lineH, attrs) {
    const space = el('text', { ...attrs, x: 0, y: -9999, opacity: 0 }, parent, 'a a');
    const one = el('text', { ...attrs, x: 0, y: -9999, opacity: 0 }, parent, 'aa');
    const gap = space.getComputedTextLength() - one.getComputedTextLength();
    space.remove(); one.remove();
    let cx = x, cy = y;
    return words.map(w => {
      const e = el('text', { ...attrs, x: f3(cx), y: f3(cy) }, parent, w);
      const len = e.getComputedTextLength();
      if (cx > x && cx + len > x + maxW) { cx = x; cy += lineH; e.setAttribute('x', f3(cx)); e.setAttribute('y', f3(cy)); }
      const out = { e, x: cx, y: cy, w: len };
      cx += len + gap;
      return out;
    });
  }

  // ---------- boot ----------
  // fonts: CSS font strings to wait for; render(t) draws one frame. The renderer calls window.__setTime.
  function boot(loop, draw, fonts = [], setup = () => {}) {
    let live = true, ready = false;
    // setup (text layout, measuring) runs once, the first frame the fonts are really there
    const render = t => {
      if (!ready && fonts.every(f => document.fonts.check(f))) { setup(); ready = true; }
      if (ready) draw(t);
    };
    window.__setTime = t => { live = false; render(((t % loop) + loop) % loop); };
    const clock = document.createElement('span');
    clock.style.cssText = 'position:absolute;width:0;height:0;overflow:hidden';
    document.body.appendChild(clock);
    // the renderer reads the loop from --loop and wants one Web Animation on the page
    clock.animate([{ opacity: 0 }, { opacity: 0 }], { duration: loop, iterations: Infinity });
    document.documentElement.style.setProperty('--loop', loop / 1000 + 's');
    const t0 = performance.now();
    const tick = now => { if (!live) return; render((now - t0) % loop); requestAnimationFrame(tick); };
    Promise.all(fonts.map(f => document.fonts.load(f))).then(() => { render(0); if (live) requestAnimationFrame(tick); });
    render(0);
  }

  return { clamp, lerp, seg, ease, f3, mix, hash, win, between, camera, focus, fit, setView, toScreen,
    speech, wordIn, voice, wave, path, pointer, click, el, set, layoutWords, boot };
})();
