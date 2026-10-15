// The liquid-glass water shader (ADR-064), ported to WebGL2 / GLSL ES 3.00 from the user's
// `animation/liquid_glass_fx (1).py` (a moderngl demo).
//
// What changed in the port: the demo draws its own wallpaper and refracts it. The overlay lies
// over the user's real screen, which HandOff does not capture, so the background is gone. The
// same water height field and lighting are kept; the result is light (specular highlights, a
// cool sheen on the slopes, the glass bead and its rim) written as premultiplied colour with
// alpha, so everything not touched by the water stays fully see-through.

/** One slot per effect the timeline can run at once (MAX_ACTIVE). */
export const SLOTS = 4;

export const VERTEX_SOURCE = `#version 300 es
in vec2 aPos;
void main() { gl_Position = vec4(aPos, 0.0, 1.0); }
`;

export const FRAGMENT_SOURCE = `#version 300 es
precision highp float;

uniform vec2  uRes;
uniform vec4  uR[${SLOTS}];   // x, y (unit square, origin bottom left), age in seconds (<0 = off), kind (0 grab, 1 drop)
uniform vec3  uHold;          // x, y, amount of the glass bead

out vec4 fragColor;

float asp;

// ---- water height field ----
float wave(vec2 p, vec4 r, out float wg, out float wd) {
    wg = 0.0; wd = 0.0;
    float age = r.z;
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
    } else {
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
    }
    return h;
}

float height(vec2 p, out float wg, out float wd) {
    float h = 0.0; wg = 0.0; wd = 0.0;
    for (int i = 0; i < ${SLOTS}; i++) {
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

    // lighting on the water
    vec3 n = normalize(vec3(-g * 2.3, 1.0));
    vec3 L1 = normalize(vec3(-0.45, 0.55, 0.70));
    vec3 L2 = normalize(vec3(0.50, -0.40, 0.75));
    float spec  = pow(max(dot(n, normalize(L1 + vec3(0.0, 0.0, 1.0))), 0.0), 110.0);
    float spec2 = pow(max(dot(n, normalize(L2 + vec3(0.0, 0.0, 1.0))), 0.0), 60.0) * 0.45;
    float slope = length(g);

    float mixG = wg / (wg + wd + 0.0001);
    // bluer than the demo's near-white tints: light on top of a light screen would vanish
    vec3 tint = mix(vec3(0.62, 0.72, 1.00), vec3(0.30, 0.82, 1.00), mixG);

    vec3 light = tint * (spec * 1.15 + spec2) * 0.9;
    light += tint * smoothstep(0.0, 0.9, slope) * 0.11;

    // a cool, faint body on the slopes so the water reads as glass on a light or a dark screen
    float body = clamp(slope * 0.55 + abs(h0) * 0.08, 0.0, 0.42);
    vec3 bodyCol = mix(vec3(0.16, 0.46, 0.95), tint, 0.35);

    // liquid glass bead under the cursor: bright glass edge and a touch of inner glow
    vec2 dv = (uv - uHold.xy) * vec2(asp, 1.0);
    float ld = length(dv);
    float amt = uHold.z;
    float lens = amt * exp(-ld * ld / 0.0125);
    float rx = (ld - 0.145) / 0.012;
    float rim = exp(-rx * rx) * amt;
    vec2 rdir = ld > 0.0001 ? dv / ld : vec2(0.0);
    float facing = 0.5 + 0.5 * dot(rdir, normalize(vec2(-0.5, 0.7)));
    light += vec3(0.80, 0.95, 1.0) * rim * (0.30 + 0.70 * facing) * 0.9;

    float glow = lens * 0.16;
    vec3 rgb = light + bodyCol * body + vec3(0.55, 0.85, 1.0) * glow;
    float a = clamp(max(max(light.r, light.g), light.b) + body + glow, 0.0, 1.0);
    fragColor = vec4(min(rgb, vec3(a)), a); // premultiplied: no channel may exceed alpha
}
`;
