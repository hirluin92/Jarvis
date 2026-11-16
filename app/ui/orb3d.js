// The 3D orb of the /jarvis screen: GPU particles + bloom (three.js, vendored in ./vendor).
// It exposes window.FX3D; the page keeps its 2D orb as a fallback if anything here fails.
import * as THREE from "three";
import {EffectComposer} from "./vendor/postprocessing/EffectComposer.js";
import {RenderPass} from "./vendor/postprocessing/RenderPass.js";
import {UnrealBloomPass} from "./vendor/postprocessing/UnrealBloomPass.js";
import {OutputPass} from "./vendor/postprocessing/OutputPass.js";

const NOISE = `
vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 permute(vec4 x){return mod289(((x*34.0)+10.0)*x);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
float snoise(vec3 v){
  const vec2 C=vec2(1.0/6.0,1.0/3.0); const vec4 D=vec4(0.0,0.5,1.0,2.0);
  vec3 i=floor(v+dot(v,C.yyy)); vec3 x0=v-i+dot(i,C.xxx);
  vec3 g=step(x0.yzx,x0.xyz); vec3 l=1.0-g; vec3 i1=min(g.xyz,l.zxy); vec3 i2=max(g.xyz,l.zxy);
  vec3 x1=x0-i1+C.xxx; vec3 x2=x0-i2+C.yyy; vec3 x3=x0-D.yyy;
  i=mod289(i);
  vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
  float n_=0.142857142857; vec3 ns=n_*D.wyz-D.xzx;
  vec4 j=p-49.0*floor(p*ns.z*ns.z); vec4 x_=floor(j*ns.z); vec4 y_=floor(j-7.0*x_);
  vec4 x=x_*ns.x+ns.yyyy; vec4 y=y_*ns.x+ns.yyyy; vec4 h=1.0-abs(x)-abs(y);
  vec4 b0=vec4(x.xy,y.xy); vec4 b1=vec4(x.zw,y.zw);
  vec4 s0=floor(b0)*2.0+1.0; vec4 s1=floor(b1)*2.0+1.0; vec4 sh=-step(h,vec4(0.0));
  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy; vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
  vec3 p0=vec3(a0.xy,h.x); vec3 p1=vec3(a0.zw,h.y); vec3 p2=vec3(a1.xy,h.z); vec3 p3=vec3(a1.zw,h.w);
  vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
  p0*=norm.x; p1*=norm.y; p2*=norm.z; p3*=norm.w;
  vec4 m=max(0.5-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0); m=m*m;
  return 105.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
}`;

const SPHERE_VERT = `
uniform float uTime,uHear,uSpeak,uThink,uBoot,uSize,uPulse,uPixel,uEnergy;
uniform float uSpec[64];
attribute float aRand;
varying float vS; varying float vR; varying float vDepth;
${NOISE}
void main(){
  vec3 d=normalize(position);
  float ang=abs(atan(d.z,d.x))/3.14159265;
  float f=ang*62.0; int i0=int(floor(f)); float fr=f-float(i0);
  float s=mix(uSpec[i0],uSpec[i0+1],fr);
  float lat=d.y*0.5+0.5;
  float n=snoise(d*1.7+vec3(0.0,uTime*0.22,uTime*0.1));
  float n2=snoise(d*4.0-vec3(uTime*0.5));
  float act=max(uHear,uSpeak);
  float r=1.0+0.025*sin(uTime*1.3)+n*0.05+n2*0.015*(0.3+act);
  r+=s*(0.6+0.6*aRand)*act*(0.8+0.6*n2);
  r+=uSpeak*uEnergy*0.12*sin(abs(d.y)*14.0-uTime*7.0);
  float w=exp(-pow((lat-uPulse)*8.0,2.0))*step(-0.05,uPulse)*step(uPulse,1.4);
  r+=0.17*w;
  vec3 p=d*r;
  float rr=fract(aRand*13.7);
  float rad=0.28+0.95*sqrt(rr);
  float a=atan(d.z,d.x)+uTime*2.6/(0.35+rad)+rad*2.2;
  vec3 ring=vec3(cos(a),(aRand-0.5)*0.16*(1.5-rad)+0.04*n,sin(a))*rad;
  p=mix(p,ring,uThink)*uBoot;
  vec4 mv=modelViewMatrix*vec4(p,1.0);
  gl_Position=projectionMatrix*mv;
  vS=s*act+w*0.9+uThink*0.3;
  vR=aRand;
  vDepth=clamp(1.0-(-mv.z-3.2)/3.0,0.18,1.0)*(1.0-0.5*uThink);
  gl_PointSize=uSize*uPixel*(1.4+2.8*aRand*aRand+2.0*vS)*(4.2/(-mv.z));
}`;
const SPHERE_FRAG = `
uniform vec3 uColor,uHot;
varying float vS; varying float vR; varying float vDepth;
void main(){
  float d=length(gl_PointCoord-0.5); if(d>0.5) discard;
  float a=pow(1.0-d*2.0,1.5);
  vec3 col=mix(uColor,uHot,clamp(vS*1.4+vR*0.12,0.0,1.0));
  gl_FragColor=vec4(col*a*(0.9+1.5*vDepth),a);
}`;

const RING_VERT = `
uniform float uTime,uBoot,uSize,uPixel,uRad,uDir,uEnergy,uHear,uSpeak;
uniform float uSpec[64];
attribute float aAng; attribute float aRand;
varying float vA;
void main(){
  float a=aAng+uTime*0.12*uDir;
  float s=uSpec[int(abs(sin(aAng))*63.0)];
  float act=max(uHear,uSpeak);
  float r=uRad+(aRand-0.5)*0.05+sin(aAng*6.0+uTime*1.5)*0.012+s*act*0.16;
  vec3 p=vec3(cos(a)*r,(aRand-0.5)*0.03+s*act*0.05*sin(aAng*9.0),sin(a)*r)*uBoot;
  vec4 mv=modelViewMatrix*vec4(p,1.0);
  gl_Position=projectionMatrix*mv;
  vA=0.2+0.8*pow(0.5+0.5*sin(aAng*3.0-uTime*1.4*uDir),4.0)+uEnergy*0.4;
  gl_PointSize=uSize*uPixel*(1.4+aRand*1.6)*(4.2/(-mv.z));
}`;
const RING_FRAG = `
uniform vec3 uColor;
varying float vA;
void main(){
  float d=length(gl_PointCoord-0.5); if(d>0.5) discard;
  float a=pow(1.0-d*2.0,1.4)*vA;
  gl_FragColor=vec4(uColor*a,a);
}`;

const CORE_VERT = `
uniform float uSize,uPixel,uEnergy,uBoot;
void main(){
  vec4 mv=modelViewMatrix*vec4(0.0,0.0,0.0,1.0);
  gl_Position=projectionMatrix*mv;
  gl_PointSize=uSize*uPixel*(140.0+uEnergy*210.0)*uBoot*(4.2/(-mv.z));
}`;
const CORE_FRAG = `
uniform vec3 uColor,uHot; uniform float uEnergy;
void main(){
  float d=length(gl_PointCoord-0.5)*2.0; if(d>1.0) discard;
  float a=pow(1.0-d,2.4);
  vec3 col=mix(uColor,uHot,pow(1.0-d,3.0));
  gl_FragColor=vec4(col*a*(0.9+uEnergy),a);
}`;

function init() {
  const canvas = document.getElementById("gl");
  const phone = innerWidth < 700;
  const renderer = new THREE.WebGLRenderer({canvas, antialias: false, alpha: false, powerPreference: "high-performance"});
  renderer.setClearColor(0x000000, 1);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
  camera.position.set(0, 0, 4.2);
  const group = new THREE.Group();
  group.scale.setScalar(0.74);
  scene.add(group);

  const spec = new Float32Array(64);
  const shared = {
    uTime: {value: 0}, uBoot: {value: 0}, uSize: {value: 1}, uPixel: {value: 1}, uEnergy: {value: 0},
    uHear: {value: 0}, uSpeak: {value: 0}, uThink: {value: 0}, uSpec: {value: spec},
    uColor: {value: new THREE.Color(0x00d9ff)}, uHot: {value: new THREE.Color(0xffffff)},
  };

  // main sphere
  const N = phone ? 7000 : 16000;
  const pos = new Float32Array(N * 3), rnd = new Float32Array(N);
  for (let i = 0; i < N; i++) {
    const y = 1 - (i + 0.5) / N * 2, r = Math.sqrt(1 - y * y), th = i * 2.399963;
    pos.set([Math.cos(th) * r, y, Math.sin(th) * r], i * 3); rnd[i] = Math.random();
  }
  const sg = new THREE.BufferGeometry();
  sg.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  sg.setAttribute("aRand", new THREE.BufferAttribute(rnd, 1));
  const sphereMat = new THREE.ShaderMaterial({
    uniforms: {...shared, uPulse: {value: 9}}, vertexShader: SPHERE_VERT, fragmentShader: SPHERE_FRAG,
    transparent: true, depthWrite: false, depthTest: false, blending: THREE.AdditiveBlending,
  });
  const sphere = new THREE.Points(sg, sphereMat); sphere.frustumCulled = false; group.add(sphere);

  // two orbiting rings of particles (gyroscope)
  const rings = [];
  for (const [rad, tiltX, tiltZ, dir] of [[1.42, 1.2, 0.2, 1], [1.7, 0.45, -0.5, -1]]) {
    const M = phone ? 1300 : 2600, p = new Float32Array(M * 3), a = new Float32Array(M), r = new Float32Array(M);
    for (let i = 0; i < M; i++) { a[i] = (i / M) * Math.PI * 2; r[i] = Math.random(); }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(p, 3));
    g.setAttribute("aAng", new THREE.BufferAttribute(a, 1)); g.setAttribute("aRand", new THREE.BufferAttribute(r, 1));
    const m = new THREE.ShaderMaterial({
      uniforms: {...shared, uRad: {value: rad}, uDir: {value: dir}}, vertexShader: RING_VERT, fragmentShader: RING_FRAG,
      transparent: true, depthWrite: false, depthTest: false, blending: THREE.AdditiveBlending,
    });
    const pts = new THREE.Points(g, m); pts.frustumCulled = false; pts.rotation.set(tiltX, 0, tiltZ);
    pts.userData = {dir, tiltX, tiltZ}; rings.push(pts); group.add(pts);
  }

  // glowing core
  const cg = new THREE.BufferGeometry();
  cg.setAttribute("position", new THREE.BufferAttribute(new Float32Array(3), 3));
  const core = new THREE.Points(cg, new THREE.ShaderMaterial({
    uniforms: shared, vertexShader: CORE_VERT, fragmentShader: CORE_FRAG,
    transparent: true, depthWrite: false, depthTest: false, blending: THREE.AdditiveBlending,
  }));
  core.frustumCulled = false; group.add(core);

  // far stars
  const S = 700, sp = new Float32Array(S * 3);
  for (let i = 0; i < S; i++) {
    const r = 7 + Math.random() * 9, u = Math.random() * 2 - 1, t = Math.random() * 6.2832, q = Math.sqrt(1 - u * u);
    sp.set([r * q * Math.cos(t), r * u, r * q * Math.sin(t)], i * 3);
  }
  const stg = new THREE.BufferGeometry(); stg.setAttribute("position", new THREE.BufferAttribute(sp, 3));
  const stars = new THREE.Points(stg, new THREE.PointsMaterial({size: 0.03, color: 0x8fd8ff, transparent: true, opacity: 0.5, depthWrite: false}));
  scene.add(stars);

  // bloom
  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(new THREE.Vector2(256, 256), 0.75, 0.35, 0.22);
  composer.addPass(bloom);
  composer.addPass(new OutputPass());

  function resize() {
    const box = canvas.parentElement.getBoundingClientRect();
    const w = Math.max(2, Math.round(box.width)), h = Math.max(2, Math.round(box.height));
    const ratio = Math.min(devicePixelRatio || 1, phone ? 1.5 : 2);
    renderer.setPixelRatio(ratio); renderer.setSize(w, h, false); composer.setPixelRatio(ratio); composer.setSize(w, h);
    camera.aspect = w / h; camera.updateProjectionMatrix();
    shared.uPixel.value = ratio * (h / 700);
  }
  new ResizeObserver(resize).observe(canvas.parentElement); resize();

  // touch / mouse: drag to turn the orb, hover to tilt the view
  let drag = false, lx = 0, ly = 0, vx = 0, vy = 0, rotY = 0, rotX = 0.25, px = 0, py = 0;
  const host = canvas.parentElement;
  host.addEventListener("pointerdown", (e) => { drag = true; lx = e.clientX; ly = e.clientY; host.setPointerCapture(e.pointerId); });
  host.addEventListener("pointerup", () => { drag = false; });
  host.addEventListener("pointermove", (e) => {
    const b = host.getBoundingClientRect(); px = (e.clientX - b.left) / b.width - 0.5; py = (e.clientY - b.top) / b.height - 0.5;
    if (!drag) return;
    vx = (e.clientX - lx) * 0.006; vy = (e.clientY - ly) * 0.006; lx = e.clientX; ly = e.clientY;
    rotY += vx; rotX = Math.max(-1.2, Math.min(1.2, rotX + vy));
  });

  let pulse = 9, think = 0, hear = 0, speak = 0, boot = 0;
  window.FX3D = {
    ok: true,
    pulse() { pulse = -0.15; },
    render(p) {
      const k = (v) => 1 - Math.exp(-v * p.dt);
      think += ((p.mode === "thinking" ? 1 : 0) - think) * k(3.2);
      hear += ((p.mode === "hearing" ? 1 : 0) - hear) * k(8);
      speak += ((p.mode === "speaking" ? 1 : 0) - speak) * k(8);
      boot += (Math.max(0.05, p.boot) - boot) * k(4);
      if (pulse < 1.5) pulse += p.dt * 1.15;
      spec.set(p.spec);
      const u = shared;
      u.uTime.value = p.t; u.uBoot.value = 0.35 + 0.65 * boot; u.uEnergy.value = p.energy;
      u.uHear.value = hear; u.uSpeak.value = speak; u.uThink.value = think;
      u.uColor.value.setRGB(p.color[0] / 255, p.color[1] / 255, p.color[2] / 255);
      u.uHot.value.setRGB(0.5 + p.color[0] / 510, 0.5 + p.color[1] / 510, 0.5 + p.color[2] / 510);
      u.uSize.value = phone ? 1.25 : 1;
      sphereMat.uniforms.uPulse.value = pulse;
      // turning: automatic, faster while thinking; a drag adds inertia
      if (!drag) { vx *= 0.95; rotY += vx + p.dt * (0.12 + think * 1.3 + speak * 0.25) * p.motion; rotX += ((think > 0.5 ? 0.95 : 0.25) - rotX) * k(1.6); }
      sphere.rotation.set(rotX, rotY, 0);
      rings.forEach((r) => { r.rotation.set(r.userData.tiltX + Math.sin(p.t * 0.3) * 0.08, 0, r.userData.tiltZ); });
      camera.position.x += ((px * 0.5) - camera.position.x) * k(2); camera.position.y += ((-py * 0.35) - camera.position.y) * k(2);
      camera.position.z += ((4.2 - speak * 0.25 - think * 0.15) - camera.position.z) * k(2);
      camera.lookAt(0, 0, 0);
      stars.rotation.y = p.t * 0.01;
      bloom.strength = 0.5 + p.energy * 0.7 + think * 0.15;
      composer.render();
    },
  };
  document.documentElement.classList.add("fx3d");
}

try { init(); } catch (err) { console.warn("3D orb unavailable, using the 2D one:", err); }
