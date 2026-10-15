"""
liquid_glass_fx.py
------------------
Liquid-glass water "grab and drop" effect for Python (Linux, Windows, macOS).

* The effect itself is a GLSL shader driven by `moderngl`, so it works inside any
  window toolkit that gives you an OpenGL 3.3 context (pygame, pyglet, glfw,
  PySide6/PyQt via QOpenGLWidget ...).
* Running this file directly opens a pygame demo window.

Install
    pip install moderngl pygame            # Pillow is optional (wallpaper loading)

Run the demo
    python liquid_glass_fx.py

Demo controls
    Mouse / touch : press and hold = GRAB, release = DROP
    SPACE         : full demo (grab, drag, drop)
    G / D         : preview only the grab / only the drop animation
    Drag an image file into the window to use it as the wallpaper, R resets it
    ESC           : quit

Use it in your own project
    fx = LiquidGlassFX(ctx, (width, height))   # ctx = moderngl.create_context()
    fx.grab(x, y)        # finger/mouse down        (pixel coords, origin top-left)
    fx.move(x, y)        # finger/mouse moving while grabbed
    fx.drop()            # finger/mouse released -> drop animation at current spot
    fx.render()          # call once per frame (draws to the screen framebuffer)
    fx.resize((w, h))    # call when the window size changes
"""

import time
from array import array

import moderngl

VERT = """
#version 330 core
in vec2 aPos;
void main() { gl_Position = vec4(aPos, 0.0, 1.0); }
"""

FRAG = """
#version 330 core

uniform vec2  uRes;
uniform float uTime;
uniform vec4  uR[12];     // x, y, startTime, kind (0 grab, 1 drop, 2 trail)
uniform vec3  uHold;      // x, y, amount
uniform sampler2D uTex;
uniform float uHasTex;
uniform float uImgA;

out vec4 fragColor;

float asp;

float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), f.x),
               mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), f.x), f.y);
}
float fbm(vec2 p) {
    float v = 0.0, a = 0.5;
    for (int i = 0; i < 4; i++) { v += a * noise(p); p = p * 2.03 + vec2(7.1, 3.7); a *= 0.5; }
    return v;
}

// ---- wallpaper (procedural silk, or the user's image) ----
vec3 bgProc(vec2 uv) {
    vec2 q = uv * vec2(asp, 1.0);
    float t = uTime * 0.045;
    float n = fbm(q * 1.5 + vec2(t, -t * 0.7));
    float bands = sin((q.x * 0.8 + q.y * 1.25 + n * 3.2) * 6.5 + t * 5.0) * 0.5 + 0.5;
    vec3 base = mix(vec3(0.015, 0.035, 0.11), vec3(0.03, 0.20, 0.40), clamp(uv.y * 0.75 + n * 0.55, 0.0, 1.0));
    base = mix(base, vec3(0.36, 0.20, 0.68), smoothstep(0.52, 0.95, n + uv.x * 0.28) * 0.5);
    base += vec3(0.08, 0.50, 0.85) * pow(bands, 7.0) * 0.38;
    base += vec3(0.05, 0.30, 0.55) * pow(1.0 - bands, 9.0) * 0.18;
    float g1 = exp(-dot(q - vec2(0.18 * asp, 0.82), q - vec2(0.18 * asp, 0.82)) * 3.2);
    float g2 = exp(-dot(q - vec2(0.88 * asp, 0.22), q - vec2(0.88 * asp, 0.22)) * 3.6);
    base += vec3(0.05, 0.55, 0.85) * g1 * 0.45 + vec3(0.55, 0.30, 0.95) * g2 * 0.4;
    return base;
}
vec3 bgS(vec2 uv) {
    if (uHasTex > 0.5) {
        vec2 s = vec2(min(1.0, asp / uImgA), min(1.0, uImgA / asp));
        vec2 tuv = (uv - 0.5) * s + 0.5;
        return texture(uTex, clamp(tuv, 0.001, 0.999)).rgb;
    }
    return bgProc(uv);
}

// ---- water height field ----
float wave(vec2 p, vec4 r, out float wg, out float wd) {
    wg = 0.0; wd = 0.0;
    float age = uTime - r.z;
    if (age < 0.0 || age > 3.2) return 0.0;
    vec2 c = r.xy * vec2(asp, 1.0);
    float d = length(p - c);
    float h = 0.0;

    if (r.w < 0.5) {
        // GRAB: rings collapse inward, accelerate, then the surface pinches shut
        float t = age / 1.35;
        if (t > 1.3) return 0.0;
        float tc = clamp(t, 0.0, 1.0);
        float R = 0.85 * (1.0 - tc * tc);
        float x = (d - R) / 0.13;
        float env = exp(-x * x);
        float amp = (0.45 + 0.95 * tc) * (1.0 - smoothstep(0.85, 1.0, t)) * smoothstep(0.0, 0.05, age);
        h = -sin((d - R) * 56.0) * env * amp;
        float pin = exp(-d * d / 0.005) * smoothstep(0.68, 1.0, t) * (1.0 - smoothstep(1.0, 1.3, t));
        h -= pin * 1.3;
        wg = abs(h);
    } else if (r.w < 1.5) {
        // DROP: a splash pops at the centre and big rings roll outward and fade
        float t = age / 2.7;
        float R = 1.2 * (1.0 - exp(-2.0 * age));
        float x = (d - R) / 0.17;
        float env = exp(-x * x);
        float k = max(1.0 - t, 0.0);
        float amp = k * k / (1.0 + R * 2.4) * 1.7 * smoothstep(0.0, 0.04, age);
        h = sin((d - R) * 46.0) * env * amp;
        float x2 = (d - R * 0.62) / 0.11;
        h += sin((d - R * 0.62) * 60.0) * exp(-x2 * x2) * amp * 0.45;
        h += exp(-d * d / 0.007) * cos(age * 17.0) * exp(-age * 3.2) * 1.0;
        wd = abs(h);
    } else {
        // TRAIL: small wake that follows a finger while it carries the grab
        float t = age / 1.3;
        float R = 0.34 * (1.0 - exp(-3.0 * age));
        float x = (d - R) / 0.07;
        float k = max(1.0 - t, 0.0);
        h = sin((d - R) * 72.0) * exp(-x * x) * 0.32 * k * k;
        wg = abs(h);
    }
    return h;
}

float height(vec2 p, out float wg, out float wd) {
    float h = 0.0; wg = 0.0; wd = 0.0;
    for (int i = 0; i < 12; i++) {
        float a, b;
        h += wave(p, uR[i], a, b);
        wg += a; wd += b;
    }
    return h;
}

void main() {
    asp = uRes.x / uRes.y;
    vec2 uv = gl_FragCoord.xy / uRes;
    vec2 p = uv * vec2(asp, 1.0);

    float wg, wd, t1, t2;
    float e = 0.0025;
    float h0  = height(p, wg, wd);
    float hx1 = height(p + vec2(e, 0.0), t1, t2);
    float hx0 = height(p - vec2(e, 0.0), t1, t2);
    float hy1 = height(p + vec2(0.0, e), t1, t2);
    float hy0 = height(p - vec2(0.0, e), t1, t2);
    vec2 g = vec2(hx1 - hx0, hy1 - hy0) / (2.0 * e) * 0.0095;

    // refraction through the water surface
    vec2 off = g * 0.055;
    off.x /= asp;

    // liquid glass bead under the finger while grabbed
    vec2 dv = (uv - uHold.xy) * vec2(asp, 1.0);
    float ld = length(dv);
    float amt = uHold.z;
    float lens = amt * exp(-ld * ld / 0.0125);
    vec2 lensOff = -(uv - uHold.xy) * lens * 0.30;
    float rx = (ld - 0.145) / 0.012;
    float rim = exp(-rx * rx) * amt;
    vec2 rdir = ld > 0.0001 ? dv / ld : vec2(0.0);
    lensOff += vec2(rdir.x / asp, rdir.y) * rim * 0.018;

    vec2 base = uv + off + lensOff;
    vec3 col;
    col.r = bgS(base).r;
    col.g = bgS(base + off * 0.10 + lensOff * 0.06).g;
    col.b = bgS(base + off * 0.22 + lensOff * 0.12).b;

    // lighting on the water
    vec3 n = normalize(vec3(-g * 2.3, 1.0));
    vec3 L1 = normalize(vec3(-0.45, 0.55, 0.70));
    vec3 L2 = normalize(vec3(0.50, -0.40, 0.75));
    float spec  = pow(max(dot(n, normalize(L1 + vec3(0.0, 0.0, 1.0))), 0.0), 110.0);
    float spec2 = pow(max(dot(n, normalize(L2 + vec3(0.0, 0.0, 1.0))), 0.0), 60.0) * 0.45;
    float slope = length(g);

    float mixG = wg / (wg + wd + 0.0001);
    vec3 tint = mix(vec3(0.92, 0.86, 1.00), vec3(0.45, 0.86, 1.00), mixG);

    col += tint * (spec * 1.15 + spec2) * 0.9;
    col += tint * smoothstep(0.0, 0.9, slope) * 0.11;
    col *= 1.0 + h0 * 0.10;

    // lens: bright glass edge, a touch of inner glow
    float facing = 0.5 + 0.5 * dot(rdir, normalize(vec2(-0.5, 0.7)));
    col += vec3(0.80, 0.95, 1.0) * rim * (0.30 + 0.70 * facing) * 0.9;
    col += vec3(0.55, 0.85, 1.0) * lens * 0.07;

    // vignette and fine grain
    vec2 vv = uv - 0.5;
    col *= 1.0 - 0.45 * dot(vv, vv);
    col += (hash(gl_FragCoord.xy + fract(uTime) * 91.0) - 0.5) * 0.014;

    fragColor = vec4(col, 1.0);
}
"""


class LiquidGlassFX:
    """Renders the liquid glass wallpaper plus grab / drop water animations."""

    _BIG_SLOTS = 4          # slots 0-3: grab and drop ripples
    _TRAIL_SLOTS = 8        # slots 4-11: small wake behind a dragged finger
    _N = 12

    def __init__(self, ctx, size):
        self.ctx = ctx
        self.w, self.h = int(size[0]), int(size[1])

        self.prog = ctx.program(vertex_shader=VERT, fragment_shader=FRAG)
        self.vbo = ctx.buffer(array("f", [-1, -1, 1, -1, -1, 1, 1, 1]).tobytes())
        self.vao = ctx.vertex_array(self.prog, [(self.vbo, "2f", "aPos")])

        self.tex = None
        self.has_tex = 0.0
        self.img_aspect = 1.0
        self._make_texture((1, 1), b"\x00\x00\x00\xff")
        self.has_tex = 0.0

        self._t0 = time.perf_counter()
        self._last = 0.0
        self._ripples = [0.0] * (4 * self._N)
        for i in range(self._N):
            self._ripples[i * 4 + 2] = -100.0
        self._next_big = 0
        self._next_trail = 0

        # the glass bead that follows the finger
        self._active = False
        self._tx = self._ty = 0.5      # target (normalised, origin bottom-left)
        self._sx = self._sy = 0.5      # smoothed
        self._amt = 0.0
        self._trail_last = (0.5, 0.5, 0.0)

    # ------------------------------------------------------------------ public
    def resize(self, size):
        self.w, self.h = max(1, int(size[0])), max(1, int(size[1]))

    def now(self):
        return time.perf_counter() - self._t0

    def grab(self, x, y):
        """Finger / mouse down at pixel (x, y). Origin is the top-left corner."""
        nx, ny = self._norm(x, y)
        self._active = True
        self._tx = self._sx = nx
        self._ty = self._sy = ny
        self._trail_last = (nx, ny, self.now())
        self._emit(nx, ny, 0)

    def move(self, x, y):
        """Finger / mouse moved while grabbed."""
        if self._active:
            self._tx, self._ty = self._norm(x, y)

    def drop(self, x=None, y=None):
        """Release. Plays the drop animation at (x, y), or where the bead currently is."""
        if x is None or y is None:
            nx, ny = self._sx, self._sy
        else:
            nx, ny = self._norm(x, y)
        self._emit(nx, ny, 1)
        self._active = False

    def cancel(self):
        """Release without the drop animation (used for the grab-only preview)."""
        self._active = False

    def set_wallpaper(self, size, rgba_bottom_up):
        """Use your own image. `rgba_bottom_up` is raw RGBA bytes, first row = BOTTOM row."""
        self._make_texture(size, rgba_bottom_up)
        self.img_aspect = size[0] / float(size[1])
        self.has_tex = 1.0

    def set_wallpaper_from_file(self, path):
        from PIL import Image, ImageOps  # pip install pillow
        img = Image.open(path).convert("RGBA")
        img.thumbnail((4096, 4096))
        img = ImageOps.flip(img)
        self.set_wallpaper(img.size, img.tobytes())

    def clear_wallpaper(self):
        self.has_tex = 0.0

    def render(self, fbo=None):
        """Draw one frame. Pass a framebuffer (e.g. from a Qt widget) or leave None for the window."""
        t = self.now()
        dt = min(t - self._last, 0.05)
        self._last = t

        k = min(1.0, dt * (7.0 if self._active else 15.0))
        self._amt += ((1.0 if self._active else 0.0) - self._amt) * k
        m = min(1.0, dt * 16.0)
        self._sx += (self._tx - self._sx) * m
        self._sy += (self._ty - self._sy) * m

        if self._active:   # little wake while the bead is dragged
            lx, ly, lt = self._trail_last
            dist = ((self._sx - lx) * self.w / self.h) ** 2 + (self._sy - ly) ** 2
            if dist > 0.07 ** 2 and t - lt > 0.17:
                self._emit(self._sx, self._sy, 2)
                self._trail_last = (self._sx, self._sy, t)

        target = fbo or self.ctx.screen
        target.use()
        target.viewport = (0, 0, self.w, self.h)

        p = self.prog
        p["uRes"].value = (float(self.w), float(self.h))
        p["uTime"].value = t
        p["uR"].write(array("f", self._ripples).tobytes())
        p["uHold"].value = (self._sx, self._sy, self._amt)
        p["uHasTex"].value = self.has_tex
        p["uImgA"].value = self.img_aspect
        p["uTex"].value = 0
        self.tex.use(0)
        self.ctx.disable(moderngl.DEPTH_TEST | moderngl.BLEND)
        self.vao.render(moderngl.TRIANGLE_STRIP)

    # ----------------------------------------------------------------- private
    def _norm(self, x, y):
        return x / float(self.w), 1.0 - y / float(self.h)

    def _emit(self, nx, ny, kind):
        if kind == 2:
            slot = self._BIG_SLOTS + self._next_trail
            self._next_trail = (self._next_trail + 1) % self._TRAIL_SLOTS
        else:
            slot = self._next_big
            self._next_big = (self._next_big + 1) % self._BIG_SLOTS
        self._ripples[slot * 4:slot * 4 + 4] = [nx, ny, self.now(), float(kind)]

    def _make_texture(self, size, data):
        if self.tex is not None:
            self.tex.release()
        self.tex = self.ctx.texture(size, 4, data)
        self.tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.tex.repeat_x = False
        self.tex.repeat_y = False


# =============================================================================
# pygame demo
# =============================================================================
def main():
    import pygame

    pygame.init()
    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 3)
    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_PROFILE_MASK, pygame.GL_CONTEXT_PROFILE_CORE)
    pygame.display.gl_set_attribute(pygame.GL_DOUBLEBUFFER, 1)
    size = (1280, 720)
    pygame.display.set_mode(size, pygame.OPENGL | pygame.DOUBLEBUF | pygame.RESIZABLE)
    pygame.display.set_caption("Liquid Grab and Drop")

    ctx = moderngl.create_context()
    fx = LiquidGlassFX(ctx, size)
    clock = pygame.time.Clock()

    demo = None            # generator driving the scripted demo
    demo_wake = 0.0
    scheduled = []         # (time, callable)

    def demo_script():
        w, h = fx.w, fx.h
        a = (w * 0.26, h * 0.40)
        b = (w * 0.74, h * 0.60)
        fx.grab(*a)
        yield 1.1
        s = time.perf_counter()
        while True:
            t = min((time.perf_counter() - s) / 1.5, 1.0)
            e = 2 * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 2 / 2
            fx.move(a[0] + (b[0] - a[0]) * e, a[1] + (b[1] - a[1]) * e)
            if t >= 1.0:
                break
            yield 0.0
        yield 0.18
        fx.drop()
        yield 2.4

    def load_wallpaper(path):
        try:
            surf = pygame.image.load(path).convert_alpha()
            try:
                data = pygame.image.tobytes(surf, "RGBA", True)
            except AttributeError:                      # older pygame
                data = pygame.image.tostring(surf, "RGBA", True)
            fx.set_wallpaper(surf.get_size(), data)
        except Exception as exc:                        # unsupported file etc.
            print("Could not load wallpaper:", exc)

    running = True
    while running:
        now = time.perf_counter()

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    running = False
                elif ev.key == pygame.K_SPACE and demo is None:
                    demo, demo_wake = demo_script(), 0.0
                elif ev.key == pygame.K_g and demo is None:
                    fx.grab(fx.w / 2, fx.h / 2)
                    scheduled.append((now + 1.5, fx.cancel))
                elif ev.key == pygame.K_d and demo is None:
                    fx.drop(fx.w / 2, fx.h / 2)
                elif ev.key == pygame.K_r:
                    fx.clear_wallpaper()
            elif ev.type == pygame.DROPFILE:
                load_wallpaper(ev.file)
            elif demo is None:
                if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                    fx.grab(*ev.pos)
                elif ev.type == pygame.MOUSEMOTION and ev.buttons[0]:
                    fx.move(*ev.pos)
                elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
                    fx.drop()

        if demo is not None and now >= demo_wake:
            try:
                demo_wake = now + next(demo)
            except StopIteration:
                demo = None

        for item in scheduled[:]:
            if now >= item[0]:
                item[1]()
                scheduled.remove(item)

        w, h = pygame.display.get_window_size()
        if (w, h) != (fx.w, fx.h):
            fx.resize((w, h))

        fx.render()
        pygame.display.flip()
        clock.tick(120)

    pygame.quit()


if __name__ == "__main__":
    main()
