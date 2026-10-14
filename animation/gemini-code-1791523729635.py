import sys
from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtCore import Qt, QTimer, QPoint, pyqtSignal, QPointF
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QRadialGradient

class LiquidGlassGrab:
    """
    Huawei-style Grab: 
    An imploding liquid glass bubble with a glowing cyan core and frosted refraction edges.
    Uses a snappy cubic ease-in to simulate sudden vacuum/suction.
    """
    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.progress = 0.0
        self.speed = 0.025 # ~40 frames (~600ms at 60fps)
        self.is_finished = False

    def update(self):
        self.progress += self.speed
        if self.progress >= 1.0:
            self.is_finished = True

    def draw(self, painter):
        if self.is_finished: return
        
        # Cubic ease-in (t^3) creates a snappy snapping implosion
        ease_in = self.progress ** 3
        current_r = 140.0 * (1.0 - ease_in)
        if current_r <= 0: return

        # Fade out at the very end to prevent a harsh pop
        alpha_factor = 1.0 if self.progress < 0.8 else (1.0 - self.progress) / 0.2
        
        # 1. Liquid Glow / Inner Bubble (Radial Gradient)
        gradient = QRadialGradient(QPointF(self.x, self.y), current_r)
        gradient.setColorAt(0.0, QColor(255, 255, 255, int(220 * alpha_factor))) # Bright white/cyan core
        gradient.setColorAt(0.4, QColor(0, 220, 255, int(150 * alpha_factor)))   # Electric cyan mid
        gradient.setColorAt(1.0, QColor(0, 150, 255, 0))                         # Transparent edge
        
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawEllipse(QPoint(int(self.x), int(self.y)), int(current_r), int(current_r))

        # 2. Refractive Glass Rim (Shrinking outline)
        rim_alpha = int(255 * (1.0 - self.progress) * alpha_factor)
        painter.setPen(QPen(QColor(200, 255, 255, rim_alpha), 3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPoint(int(self.x), int(self.y)), int(current_r * 0.95), int(current_r * 0.95))


class IQOOWaveletDrop:
    """
    iQOO Fingerprint-style Drop: 
    A central energy burst followed by 5 high-frequency expanding neon wavelets (ripples),
    alternating in thickness.
    """
    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.progress = 0.0
        self.speed = 0.015 # ~66 frames (~1000ms duration)
        self.is_finished = False
        self.max_radius = 180.0
        self.num_wavelets = 5

    def update(self):
        self.progress += self.speed
        if self.progress >= 1.0:
            self.is_finished = True

    def draw(self, painter):
        if self.is_finished: return
        
        # 1. Core Energy Burst (A glowing pulse that fades out quickly at the start)
        if self.progress < 0.4:
            burst_progress = self.progress / 0.4
            burst_r = 60.0 * (1.0 + burst_progress)
            burst_alpha = int(255 * (1.0 - burst_progress))
            
            burst_grad = QRadialGradient(QPointF(self.x, self.y), burst_r)
            burst_grad.setColorAt(0.0, QColor(150, 255, 255, burst_alpha))
            burst_grad.setColorAt(1.0, QColor(0, 200, 255, 0))
            
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(burst_grad))
            painter.drawEllipse(QPoint(int(self.x), int(self.y)), int(burst_r), int(burst_r))

        # 2. Expanding High-Frequency Wavelets
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(self.num_wavelets):
            # Stagger the start times of each concentric ripple
            delay = i * 0.08
            local_t = self.progress - delay
            
            if 0 < local_t < (1.0 - delay):
                # Normalize local time for this specific ripple
                normalized_t = local_t / (1.0 - delay)
                
                # Ease out quad for smooth deceleration as the ripple expands
                ease_out = 1.0 - (1.0 - normalized_t) ** 2
                r = self.max_radius * ease_out
                
                # Fading opacity based on expansion distance
                alpha = int(255 * (1.0 - normalized_t))
                
                # Alternating thick and thin neon lines for that optical fingerprint scanner look
                thickness = 2.5 if i % 2 == 0 else 1.0
                
                painter.setPen(QPen(QColor(0, 240, 255, alpha), thickness))
                painter.drawEllipse(QPoint(int(self.x), int(self.y)), int(r), int(r))


class TransparentOverlay(QWidget):
    trigger_grab = pyqtSignal(int, int)
    trigger_drop = pyqtSignal(int, int)

    def __init__(self):
        super().__init__()
        
        # Frameless, always on top, and click-through
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint | 
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.Tool | 
            Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # Cover the entire primary screen
        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(screen)
        
        self.effects = []
        
        # Thread-safe slots
        self.trigger_grab.connect(self._add_grab_effect)
        self.trigger_drop.connect(self._add_drop_effect)
        
        # Start animation loop at 60 FPS
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_animations)
        self.timer.start(16) 

    def _add_grab_effect(self, x, y):
        self.effects.append(LiquidGlassGrab(x, y))
        
    def _add_drop_effect(self, x, y):
        self.effects.append(IQOOWaveletDrop(x, y))

    def update_animations(self):
        if not self.effects:
            return
            
        for effect in self.effects[:]:
            effect.update()
            if effect.is_finished:
                self.effects.remove(effect)
                
        self.update() # Triggers paintEvent

    def paintEvent(self, event):
        if not self.effects:
            return
            
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        for effect in self.effects:
            effect.draw(painter)
        painter.end()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    overlay = TransparentOverlay()
    overlay.show()
    
    # --- DEMO SEQUENCE ---
    # Trigger Grab (Huawei Liquid Glass) at 1 second
    QTimer.singleShot(1000, lambda: overlay.trigger_grab.emit(500, 500))
    
    # Trigger Drop (iQOO Fingerprint Wavelet) at 3 seconds
    QTimer.singleShot(3000, lambda: overlay.trigger_drop.emit(1200, 500))
    
    print("Overlay running. Animations will play at 1s and 3s. Press Ctrl+C in terminal to exit.")
    sys.exit(app.exec())