/* =========================================================================
   ГеоШтурман — 3D-сцены на Three.js (рельеф + поверхность корреляции).
   Локальный three.min.js (vendor), без модулей. Экспорт: window.GeoScenes
   ========================================================================= */
(function () {
  "use strict";
  if (!window.THREE) { console.warn("THREE не загружен"); return; }
  const THREE = window.THREE;

  /* --------- детерминированный шум рельефа (сумма синусов) --------------- */
  function makeNoise(seed) {
    let s = seed >>> 0;
    const rnd = () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
    const oct = [];
    for (let i = 0; i < 6; i++) {
      oct.push({ fx: (0.4 + rnd() * 1.6) * (i + 1), fy: (0.4 + rnd() * 1.6) * (i + 1),
                 px: rnd() * 6.28, py: rnd() * 6.28, a: 1 / (i + 1) });
    }
    return (u, v) => {
      let h = 0, n = 0;
      for (const o of oct) {
        h += o.a * Math.sin(u * o.fx + o.px) * Math.cos(v * o.fy + o.py);
        n += o.a;
      }
      return h / n; // -1..1
    };
  }

  /* ============================ РЕЛЬЕФ ================================== */
  const Terrain = {
    canvas: null, renderer: null, scene: null, camera: null,
    plane: null, line: null, rings: null, craft: null, t: 0, raf: 0,
    noise: makeNoise(1337), azimuth: 73, craftType: "uav",

    init(canvas) {
      this.canvas = canvas;
      const r = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
      r.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      this.renderer = r;
      const sc = new THREE.Scene();
      sc.fog = new THREE.FogExp2(0x05080f, 0.018);
      this.scene = sc;

      const cam = new THREE.PerspectiveCamera(46, 1, 0.1, 400);
      cam.position.set(0, 34, 52);
      cam.lookAt(0, -2, 0);
      this.camera = cam;

      sc.add(new THREE.AmbientLight(0x4a6075, 0.9));
      const dl = new THREE.DirectionalLight(0x9fd8ff, 1.1);
      dl.position.set(-30, 50, 20); sc.add(dl);
      const gl = new THREE.PointLight(0x34d399, 0.7, 120); gl.position.set(10, 18, -6); sc.add(gl);

      this._buildTerrain();
      this._buildTrajectory();
      this._buildCraft();
      this.resize();
      const loop = () => { this.raf = requestAnimationFrame(loop); this._tick(); };
      loop();
    },

    _buildTerrain() {
      const SZ = 90, SEG = 120, A = 9;
      const geo = new THREE.PlaneGeometry(SZ, SZ, SEG, SEG);
      geo.rotateX(-Math.PI / 2);
      const pos = geo.attributes.position;
      const colors = [];
      const col = new THREE.Color();
      for (let i = 0; i < pos.count; i++) {
        const x = pos.getX(i), z = pos.getZ(i);
        const u = (x / SZ) * 6, v = (z / SZ) * 6;
        let h = this.noise(u, v) * A;
        const edge = 1 - Math.min(1, (Math.abs(x) + Math.abs(z)) / SZ); // приподнять центр
        h *= 0.5 + edge;
        pos.setY(i, h);
        const t = THREE.MathUtils.clamp((h + A) / (2 * A), 0, 1);
        col.setRGB(0.03 + t * 0.10, 0.07 + t * 0.22, 0.10 + t * 0.18);
        colors.push(col.r, col.g, col.b);
      }
      geo.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
      geo.computeVertexNormals();
      const mat = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.95, metalness: 0.05, flatShading: true });
      this.plane = new THREE.Mesh(geo, mat);
      this.scene.add(this.plane);

      // тонкая контурная сетка поверх
      const wire = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: 0x1b4a55, wireframe: true, transparent: true, opacity: 0.12 }));
      this.scene.add(wire);
      this._heightAt = (x, z) => {
        const u = (x / SZ) * 6, v = (z / SZ) * 6;
        const edge = 1 - Math.min(1, (Math.abs(x) + Math.abs(z)) / SZ);
        return this.noise(u, v) * A * (0.5 + edge);
      };
    },

    _buildTrajectory() {
      const pts = [];
      const az = (this.azimuth * Math.PI) / 180;
      const dx = Math.sin(az), dz = -Math.cos(az);
      for (let s = -38; s <= 38; s += 1.2) {
        const x = dx * s, z = dz * s;
        pts.push(new THREE.Vector3(x, this._heightAt(x, z) + 1.4, z));
      }
      const curve = new THREE.CatmullRomCurve3(pts);
      const tube = new THREE.TubeGeometry(curve, 120, 0.32, 8, false);
      const mat = new THREE.MeshBasicMaterial({ color: 0x46e88a });
      this.line = new THREE.Mesh(tube, mat);
      this.scene.add(this.line);
      // свечение
      const glow = new THREE.Mesh(new THREE.TubeGeometry(curve, 120, 0.9, 8, false),
        new THREE.MeshBasicMaterial({ color: 0x34d399, transparent: true, opacity: 0.18 }));
      this.scene.add(glow);
      this._curve = curve;

      // пульсирующие кольца цели
      const g = new THREE.Group();
      for (let k = 0; k < 2; k++) {
        const ring = new THREE.Mesh(
          new THREE.RingGeometry(1.6 + k * 1.2, 1.9 + k * 1.2, 40),
          new THREE.MeshBasicMaterial({ color: 0xe8ff5a, transparent: true, opacity: 0.8, side: THREE.DoubleSide }));
        ring.rotation.x = -Math.PI / 2;
        g.add(ring);
      }
      this.rings = g; this.scene.add(g);
    },

    _buildCraft() {
      if (this.craft) { this.scene.add(this.craft); return; }
      this.craft = this._makeCraftMesh(this.craftType);
      this.scene.add(this.craft);
    },

    _makeCraftMesh(type) {
      const g = new THREE.Group();
      const mat = new THREE.MeshStandardMaterial({ color: 0xeef4fb, roughness: 0.5, metalness: 0.3,
        emissive: 0x10202c, emissiveIntensity: 0.4 });
      if (type === "aircraft") {
        // фюзеляж + крыло + хвост (самолёт)
        const body = new THREE.Mesh(new THREE.CylinderGeometry(0.28, 0.28, 3.4, 12), mat);
        body.rotation.x = Math.PI / 2; g.add(body);
        const nose = new THREE.Mesh(new THREE.ConeGeometry(0.28, 0.9, 12), mat);
        nose.rotation.x = -Math.PI / 2; nose.position.z = 2.1; g.add(nose);
        const wing = new THREE.Mesh(new THREE.BoxGeometry(5.0, 0.1, 0.9), mat); g.add(wing);
        const tail = new THREE.Mesh(new THREE.BoxGeometry(1.8, 0.1, 0.5), mat); tail.position.z = -1.5; g.add(tail);
        const fin = new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.7, 0.6), mat); fin.position.set(0, 0.35, -1.5); g.add(fin);
      } else {
        // квадрокоптер (БПЛА)
        const core = new THREE.Mesh(new THREE.BoxGeometry(0.9, 0.35, 0.9), mat); g.add(core);
        const arm = new THREE.MeshStandardMaterial({ color: 0x9fb2c6, roughness: 0.6 });
        for (const [ax, az] of [[1, 1], [1, -1], [-1, 1], [-1, -1]]) {
          const a = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.07, 2.0, 8), arm);
          a.rotation.z = Math.PI / 2; a.rotation.y = Math.PI / 4 * (ax * az > 0 ? 1 : -1);
          a.position.set(ax * 0.7, 0, az * 0.7); g.add(a);
          const rotor = new THREE.Mesh(new THREE.CylinderGeometry(0.55, 0.55, 0.05, 18),
            new THREE.MeshBasicMaterial({ color: 0x34d399, transparent: true, opacity: 0.35 }));
          rotor.position.set(ax * 1.25, 0.2, az * 1.25); g.add(rotor);
        }
      }
      g.scale.setScalar(1.15);
      return g;
    },

    setCraftType(type) {
      this.craftType = type;
      if (this.craft) { this.scene.remove(this.craft); this.craft = null; }
      this.craft = this._makeCraftMesh(type);
      this.scene.add(this.craft);
    },

    setAzimuth(az) {
      if (typeof az !== "number" || isNaN(az)) return;
      this.azimuth = az;
      if (this.line) { this.scene.remove(this.line); }
      // перестроить траекторию под новый азимут
      this._rebuildTrajectory();
    },
    _rebuildTrajectory() {
      // удалить старые объекты траектории
      ["line", "rings"].forEach(k => { if (this[k]) { this.scene.remove(this[k]); this[k] = null; } });
      this._buildTrajectory();
    },

    _tick() {
      this.t += 0.005;
      // лёгкое орбитальное движение камеры
      const a = Math.sin(this.t * 0.4) * 0.18;
      this.camera.position.x = Math.sin(a) * 60;
      this.camera.position.z = Math.cos(a) * 60;
      this.camera.position.y = 34 + Math.sin(this.t * 0.5) * 3;
      this.camera.lookAt(0, -1, 0);

      // самолёт движется вдоль кривой
      if (this.craft && this._curve) {
        const p = (this.t * 0.06) % 1;
        const pos = this._curve.getPointAt(p);
        const tan = this._curve.getTangentAt(p);
        this.craft.position.copy(pos).add(new THREE.Vector3(0, 0.6, 0));
        this.craft.lookAt(pos.clone().add(tan));
        if (this.rings) this.rings.position.set(pos.x, this._heightAt(pos.x, pos.z) + 0.1, pos.z);
      }
      if (this.rings) {
        const s = 1 + Math.sin(this.t * 3) * 0.12;
        this.rings.scale.setScalar(s);
        this.rings.children.forEach((r, i) => { r.material.opacity = 0.4 + 0.4 * Math.abs(Math.sin(this.t * 2 + i)); });
      }
      this.renderer.render(this.scene, this.camera);
    },

    resize() {
      if (!this.canvas) return;
      const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
      if (!w || !h) return;
      this.renderer.setSize(w, h, false);
      this.camera.aspect = w / h; this.camera.updateProjectionMatrix();
    },
  };

  /* ===================== ПОВЕРХНОСТЬ КОРРЕЛЯЦИИ ========================= */
  const Corr = {
    canvas: null, renderer: null, scene: null, camera: null, mesh: null, wire: null,
    peak: null, t: 0, raf: 0, peakU: 0.55, peakV: 0.5,

    init(canvas) {
      this.canvas = canvas;
      const r = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
      r.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      this.renderer = r;
      this.scene = new THREE.Scene();
      const cam = new THREE.PerspectiveCamera(45, 1, 0.1, 200);
      cam.position.set(0, 26, 34); cam.lookAt(0, 2, 0);
      this.camera = cam;
      this.scene.add(new THREE.AmbientLight(0xffffff, 0.9));
      this._build();
      this.resize();
      const loop = () => { this.raf = requestAnimationFrame(loop); this._tick(); };
      loop();
    },

    _colormap(t) {
      // magma-подобная: тёмно-фиолетовый → пурпур → оранжевый → жёлтый
      t = THREE.MathUtils.clamp(t, 0, 1);
      const stops = [
        [0.02, 0.01, 0.10], [0.25, 0.05, 0.36], [0.55, 0.08, 0.50],
        [0.85, 0.20, 0.36], [0.99, 0.55, 0.20], [0.99, 0.92, 0.55],
      ];
      const f = t * (stops.length - 1), i = Math.floor(f), k = f - i;
      const a = stops[i], b = stops[Math.min(i + 1, stops.length - 1)];
      return [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k];
    },

    _field(u, v) {
      const dx = u - this.peakU, dy = v - this.peakV;
      const main = Math.exp(-(dx * dx * 26 + dy * dy * 26));
      const ripple = 0.12 * Math.exp(-(dx * dx + dy * dy) * 4) * Math.cos((u + v) * 22);
      const bg = 0.06 * Math.sin(u * 14) * Math.cos(v * 13);
      return THREE.MathUtils.clamp(main + ripple + bg + 0.04, 0, 1);
    },

    _build() {
      const SZ = 26, SEG = 70, H = 11;
      const geo = new THREE.PlaneGeometry(SZ, SZ, SEG, SEG);
      geo.rotateX(-Math.PI / 2);
      const pos = geo.attributes.position;
      const colors = [], col = new THREE.Color();
      for (let i = 0; i < pos.count; i++) {
        const x = pos.getX(i), z = pos.getZ(i);
        const u = x / SZ + 0.5, v = z / SZ + 0.5;
        const f = this._field(u, v);
        pos.setY(i, f * H);
        const c = this._colormap(f);
        col.setRGB(c[0], c[1], c[2]); colors.push(col.r, col.g, col.b);
      }
      geo.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
      geo.computeVertexNormals();
      this.mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ vertexColors: true }));
      this.scene.add(this.mesh);
      this.wire = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: 0x000000, wireframe: true, transparent: true, opacity: 0.10 }));
      this.scene.add(this.wire);

      // маркер пика (лучшее совпадение)
      const px = (this.peakU - 0.5) * SZ, pz = (this.peakV - 0.5) * SZ;
      const m = new THREE.Mesh(new THREE.SphereGeometry(0.5, 16, 16), new THREE.MeshBasicMaterial({ color: 0xb6ff3a }));
      m.position.set(px, this._field(this.peakU, this.peakV) * H + 0.6, pz);
      this.peak = m; this.scene.add(m);
    },

    setPeak(u, v) {
      this.peakU = THREE.MathUtils.clamp(u, 0.1, 0.9);
      this.peakV = THREE.MathUtils.clamp(v, 0.1, 0.9);
      if (this.mesh) { this.scene.remove(this.mesh); this.scene.remove(this.wire); this.scene.remove(this.peak); }
      this._build();
    },

    _tick() {
      this.t += 0.004;
      const a = this.t * 0.25;
      this.camera.position.x = Math.sin(a) * 34;
      this.camera.position.z = Math.cos(a) * 34;
      this.camera.position.y = 24;
      this.camera.lookAt(0, 2.4, 0);
      if (this.peak) this.peak.scale.setScalar(1 + Math.sin(this.t * 4) * 0.25);
      this.renderer.render(this.scene, this.camera);
    },

    resize() {
      if (!this.canvas) return;
      const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
      if (!w || !h) return;
      this.renderer.setSize(w, h, false);
      this.camera.aspect = w / h; this.camera.updateProjectionMatrix();
    },
  };

  /* ----------------------------- API ----------------------------------- */
  window.GeoScenes = {
    initTerrain: (c) => Terrain.init(c),
    initCorr: (c) => Corr.init(c),
    setAzimuth: (az) => Terrain.setAzimuth(az),
    setCraftType: (t) => Terrain.setCraftType(t),
    setCorrPeak: (u, v) => Corr.setPeak(u, v),
    resize: () => { Terrain.resize(); Corr.resize(); },
  };
})();
