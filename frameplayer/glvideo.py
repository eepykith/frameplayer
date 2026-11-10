"""mpv video surface. Flat video is drawn by libmpv. 360/180 is a second pass."""

from __future__ import annotations

import math

from PySide6.QtCore import QByteArray, Qt, Signal
from PySide6.QtGui import QOpenGLContext
from PySide6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLFramebufferObject,
    QOpenGLFramebufferObjectFormat,
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLVertexArrayObject,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from frameplayer.shaders import FRAGMENT, LAYOUT_UNIFORM, PROJ_UNIFORM, VERTEX

try:
    import mpv
except ImportError:  # pragma: no cover
    mpv = None


def _proc_address(_ctx, name):
    context = QOpenGLContext.currentContext()
    if context is None:
        return 0
    if isinstance(name, bytes):
        qname = QByteArray(name)
    else:
        qname = QByteArray(str(name).encode())
    address = context.getProcAddress(qname)
    return int(address) if address else 0


class VideoWidget(QOpenGLWidget):
    clicked = Signal()
    double_clicked = Signal()
    look_dragged = Signal(float, float)
    ready = Signal()
    failed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setUpdateBehavior(QOpenGLWidget.UpdateBehavior.PartialUpdate)
        self.setMinimumSize(320, 180)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mpv = None
        self.ctx = None
        self._proc = None
        self._pending_log = None
        self.shader_error = ""
        self._program = None
        self._fbo = None
        self._fbo_size = (0, 0)
        self._vao = None
        self._vbo = None
        self.frame_width = 0
        self.frame_height = 0
        self.spherical = False
        self.layout = "packed"
        self.projection = "flat"
        self.yaw = 0.0
        self.pitch = 0.0
        self.fov = math.radians(90)
        self._press = None
        self._last = None
        self._dragged = False
        self._gl = None

    def attach_player(self, player) -> None:
        self.mpv = player
        if self.ctx is None and self.isValid():
            self.makeCurrent()
            self._create_context()
            self.doneCurrent()

    def set_frame_size(self, width: int, height: int) -> None:
        self.frame_width = max(0, int(width))
        self.frame_height = max(0, int(height))

    def set_view(self, *, spherical: bool, layout: str, projection: str, yaw: float, pitch: float, fov_deg: float) -> None:
        self.spherical = spherical
        self.layout = layout
        self.projection = projection
        self.yaw = yaw
        self.pitch = max(-1.4, min(1.4, pitch))
        self.fov = math.radians(max(40, min(120, fov_deg)))
        self.update()

    def initializeGL(self) -> None:
        self._gl = self.context().extraFunctions()
        self._gl.initializeOpenGLFunctions()
        if self.mpv is not None:
            self._create_context()
        self._build_shader()

    def _create_context(self) -> None:
        if mpv is None or self.mpv is None:
            self.failed.emit("libmpv is not available.")
            return
        if self.ctx is not None:
            return
        self._proc = mpv.MpvGlGetProcAddressFn(_proc_address)
        try:
            self.ctx = mpv.MpvRenderContext(
                self.mpv,
                "opengl",
                opengl_init_params={"get_proc_address": self._proc},
            )
        except Exception as exc:
            self.failed.emit(f"Could not create the mpv video surface: {exc}")
            return
        self.ctx.update_cb = self._on_mpv_update
        self.ready.emit()

    def _on_mpv_update(self) -> None:
        # mpv calls this off the GUI thread.
        self.update()

    def _build_shader(self) -> None:
        program = QOpenGLShaderProgram(self)
        ok_v = program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, VERTEX)
        ok_f = program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, FRAGMENT)
        program.bindAttributeLocation("pos", 0)
        program.bindAttributeLocation("uv", 1)
        ok_l = program.link()
        if not (ok_v and ok_f and ok_l):
            self.shader_error = program.log() or "VR projection shader failed to compile."
            self._program = None
            return
        self.shader_error = ""
        self._program = program
        self._vao = QOpenGLVertexArrayObject(self)
        self._vao.create()
        self._vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._vbo.create()
        self._vao.bind()
        self._vbo.bind()
        # pos.xy, uv.xy. uv is image space: 0,0 is the top-left of the view.
        import array

        data = array.array(
            "f",
            [
                -1,
                -1,
                0,
                1,
                1,
                -1,
                1,
                1,
                -1,
                1,
                0,
                0,
                1,
                1,
                1,
                0,
            ],
        )
        self._vbo.allocate(data.tobytes(), len(data) * 4)
        program.bind()
        program.enableAttributeArray(0)
        program.setAttributeBuffer(0, 0x1406, 0, 2, 16)
        program.enableAttributeArray(1)
        program.setAttributeBuffer(1, 0x1406, 8, 2, 16)
        program.release()
        self._vbo.release()
        self._vao.release()

    def _set_vr_uniforms(self, program, aspect: float) -> None:
        def loc(name: str) -> int:
            return program.uniformLocation(name.encode("ascii"))

        program.setUniformValue1i(loc("u_frame"), 0)
        program.setUniformValue1i(loc("u_layout"), LAYOUT_UNIFORM.get(self.layout, 0))
        program.setUniformValue1i(loc("u_proj"), PROJ_UNIFORM.get(self.projection, 1))
        program.setUniformValue1f(loc("u_yaw"), float(self.yaw))
        program.setUniformValue1f(loc("u_pitch"), float(self.pitch))
        program.setUniformValue1f(loc("u_fov"), float(self.fov))
        program.setUniformValue1f(loc("u_aspect"), float(aspect))

    def _ensure_fbo(self, width: int, height: int) -> bool:
        width = max(2, min(width, 8192))
        height = max(2, min(height, 8192))
        if self._fbo is not None and self._fbo_size == (width, height):
            return True
        if self._fbo is not None:
            self._fbo.release()
            self._fbo = None
        fmt = QOpenGLFramebufferObjectFormat()
        fmt.setAttachment(QOpenGLFramebufferObject.Attachment.NoAttachment)
        self._fbo = QOpenGLFramebufferObject(width, height, fmt)
        self._fbo_size = (width, height)
        return self._fbo.isValid()

    def paintGL(self) -> None:
        ratio = self.devicePixelRatioF()
        win_w = max(1, int(self.width() * ratio))
        win_h = max(1, int(self.height() * ratio))
        gl = self._gl
        if gl is not None:
            gl.glViewport(0, 0, win_w, win_h)
            gl.glClearColor(0.05, 0.06, 0.08, 1)
            gl.glClear(0x00004000)
        if self.ctx is None:
            return
        use_shader = self.spherical and self._program is not None and self.frame_width > 0 and self.frame_height > 0
        if not use_shader:
            self.ctx.render(
                flip_y=True,
                opengl_fbo={"fbo": int(self.defaultFramebufferObject()), "w": win_w, "h": win_h},
            )
            self.ctx.report_swap()
            return
        if not self._ensure_fbo(self.frame_width, self.frame_height):
            self.ctx.render(
                flip_y=True,
                opengl_fbo={"fbo": int(self.defaultFramebufferObject()), "w": win_w, "h": win_h},
            )
            self.ctx.report_swap()
            return
        self._fbo.bind()
        fw, fh = self._fbo_size
        self.ctx.render(flip_y=True, opengl_fbo={"fbo": int(self._fbo.handle()), "w": fw, "h": fh})
        self._fbo.release()
        gl.glBindFramebuffer(0x8D40, int(self.defaultFramebufferObject()))
        gl.glViewport(0, 0, win_w, win_h)
        gl.glActiveTexture(0x84C0)
        gl.glBindTexture(0x0DE1, int(self._fbo.texture()))
        gl.glTexParameteri(0x0DE1, 0x2801, 0x2601)  # GL_TEXTURE_MIN_FILTER, GL_LINEAR
        gl.glTexParameteri(0x0DE1, 0x2800, 0x2601)  # MAG_FILTER
        gl.glTexParameteri(0x0DE1, 0x2802, 0x812F)  # WRAP_S, CLAMP_TO_EDGE
        gl.glTexParameteri(0x0DE1, 0x2803, 0x812F)
        program = self._program
        program.bind()
        try:
            self._set_vr_uniforms(program, float(win_w) / float(win_h))
        except Exception as exc:
            self.shader_error = str(exc)
            self._program = None
            program.release()
            self.ctx.report_swap()
            return
        self._vao.bind()
        gl.glDrawArrays(0x0005, 0, 4)  # GL_TRIANGLE_STRIP
        self._vao.release()
        program.release()
        self.ctx.report_swap()

    def shutdown(self) -> None:
        self.makeCurrent()
        if self.ctx is not None:
            self.ctx.free()
            self.ctx = None
        if self.mpv is not None:
            self.mpv.terminate()
            self.mpv = None
        self.doneCurrent()

    def mousePressEvent(self, event) -> None:
        self._press = event.position()
        self._last = event.position()
        self._dragged = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._press is not None and self.spherical:
            delta = event.position() - self._last
            if (event.position() - self._press).manhattanLength() > 5:
                self._dragged = True
                self.look_dragged.emit(delta.x(), delta.y())
                self._last = event.position()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if not self._dragged and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        self._press = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)
