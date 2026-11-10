"""GL and GLES shaders for 360° and 180° VR frames.

The math matches ``frameplayer.presentation.look_uv``. Flat stereo does not use
this shader; mpv crops that in the video output. Spherical video is decoded at
full resolution (up to 8K) into a texture, then this shader samples one view.
"""

from __future__ import annotations

VERTEX = """
attribute vec2 pos;
attribute vec2 uv;
varying vec2 v_uv;
void main() {
    v_uv = uv;
    gl_Position = vec4(pos, 0.0, 1.0);
}
"""

FRAGMENT = """
varying vec2 v_uv;
uniform sampler2D u_frame;
uniform int u_layout;      // 0 packed, 1 sbs-left, 2 sbs-right, 3 tb-top, 4 tb-bottom
uniform int u_proj;        // 1 = 360, 2 = 180
uniform float u_yaw;
uniform float u_pitch;
uniform float u_fov;
uniform float u_aspect;

vec2 eye(vec2 uv) {
    if (u_layout == 1) return vec2(uv.x * 0.5, uv.y);
    if (u_layout == 2) return vec2(uv.x * 0.5 + 0.5, uv.y);
    if (u_layout == 3) return vec2(uv.x, uv.y * 0.5);
    if (u_layout == 4) return vec2(uv.x, uv.y * 0.5 + 0.5);
    return uv;
}

void main() {
    float aspect = max(u_aspect, 0.1);
    vec2 ndc = vec2(v_uv.x * 2.0 - 1.0, (1.0 - v_uv.y) * 2.0 - 1.0);
    float half_tan = tan(u_fov * 0.5);
    vec3 dir = normalize(vec3(ndc.x * half_tan, ndc.y * half_tan / aspect, 1.0));
    float cp = cos(u_pitch);
    float sp = sin(u_pitch);
    float y2 = dir.y * cp - dir.z * sp;
    float z2 = dir.y * sp + dir.z * cp;
    float cy = cos(u_yaw);
    float sy = sin(u_yaw);
    float x3 = dir.x * cy + z2 * sy;
    float z3 = -dir.x * sy + z2 * cy;
    float lon = atan(x3, z3);
    float lat = asin(clamp(y2, -1.0, 1.0));
    float h_range = (u_proj == 2) ? 3.14159265 : 6.2831853;
    float u = lon / h_range + 0.5;
    float v = 0.5 - lat / 3.14159265;
    if (u_proj == 2 && (u < 0.0 || u > 1.0)) {
        gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0);
        return;
    }
    if (u_proj != 2) {
        u = u - floor(u);
    }
    gl_FragColor = texture2D(u_frame, eye(vec2(u, v)));
}
"""

# Desktop GL and GLES both accept this legacy pair when compiled as the
# compatibility / ES2 path. QOpenGLShader prepends a version on some drivers,
# so keep the source free of a #version line and avoid ES3-only syntax.


def sources() -> tuple[str, str]:
    return VERTEX, FRAGMENT


LAYOUT_UNIFORM = {
    "packed": 0,
    "sbs-left": 1,
    "sbs-right": 2,
    "tb-top": 3,
    "tb-bottom": 4,
}
PROJ_UNIFORM = {"equirect-360": 1, "equirect-180": 2, "flat": 0}
