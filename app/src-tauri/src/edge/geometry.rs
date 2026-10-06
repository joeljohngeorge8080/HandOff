//! Where the HandOff edge window lives. Pure functions only (no windowing calls), so DPI
//! scaling and multi-monitor rules are unit-tested without a display.
//!
//! All rectangles are in *physical* pixels. Sizes are specified in *logical* pixels and scaled
//! per monitor, so the strip is the same physical size on a 1.0x and a 2.0x display. The
//! "about 2 cm" strip is derived from the CSS reference of 96 logical px per inch; no physical
//! measurement is hard-coded.

use serde::{Deserialize, Serialize};

/// CSS reference pixel density: 96 logical px per inch, 2.54 cm per inch.
const LOGICAL_PX_PER_CM: f64 = 96.0 / 2.54;
/// The drop strip is about 2 cm wide.
const ARMED_WIDTH_CM: f64 = 2.0;
const IDLE_WIDTH_LOGICAL: f64 = 6.0;
const PANEL_WIDTH_LOGICAL: f64 = 360.0;
const PANEL_MAX_HEIGHT_LOGICAL: f64 = 600.0;
const STAGE_WIDTH_LOGICAL: f64 = 420.0;
/// How far from the right edge a drag starts to wake the strip (includes the strip itself).
const APPROACH_LOGICAL: f64 = 140.0;
/// The outermost pixels where resting the pointer shows the handle.
const EDGE_BAND_LOGICAL: f64 = 8.0;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Rect {
    pub x: i32,
    pub y: i32,
    pub w: u32,
    pub h: u32,
}

impl Rect {
    pub fn right(&self) -> i32 {
        self.x + self.w as i32
    }
    pub fn bottom(&self) -> i32 {
        self.y + self.h as i32
    }
    pub fn contains(&self, px: f64, py: f64) -> bool {
        px >= self.x as f64
            && px < self.right() as f64
            && py >= self.y as f64
            && py < self.bottom() as f64
    }
}

/// One monitor: its rectangle in the virtual desktop and its scale factor.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Screen {
    pub rect: Rect,
    pub scale: f64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Mode {
    /// A hairline at the edge. Click-through: the desktop works as if it were not there.
    Idle,
    /// The 2 cm drop target. Hittable so the OS can deliver a drag-and-drop to it.
    Armed,
    /// The compact control panel (devices, destination folder, history).
    Panel,
    /// Wider canvas for the send/receive animations. Click-through.
    Stage,
}

impl Mode {
    /// Whether the window receives pointer events (otherwise they pass to the desktop).
    pub fn hittable(self) -> bool {
        matches!(self, Mode::Armed | Mode::Panel)
    }
    /// Only the panel takes keyboard focus; the strip must never steal it.
    pub fn focusable(self) -> bool {
        self == Mode::Panel
    }
}

fn px(logical: f64, scale: f64) -> u32 {
    (logical * scale).round().max(1.0) as u32
}

/// The 2 cm strip width in logical pixels.
pub fn armed_width_logical() -> f64 {
    (ARMED_WIDTH_CM * LOGICAL_PX_PER_CM).round()
}

/// Window rectangle for a mode on a screen: flush against the right edge, vertically centred.
pub fn layout(screen: &Screen, mode: Mode) -> Rect {
    let s = screen.scale;
    let (w_logical, h) = match mode {
        Mode::Idle => (IDLE_WIDTH_LOGICAL, (screen.rect.h as f64 * 0.5).round()),
        Mode::Armed => (armed_width_logical(), (screen.rect.h as f64 * 0.55).round()),
        Mode::Stage => (STAGE_WIDTH_LOGICAL, (screen.rect.h as f64 * 0.55).round()),
        Mode::Panel => (
            PANEL_WIDTH_LOGICAL,
            (screen.rect.h as f64 * 0.72)
                .min(PANEL_MAX_HEIGHT_LOGICAL * s)
                .round(),
        ),
    };
    let w = px(w_logical, s).min(screen.rect.w);
    let h = (h as u32).clamp(1, screen.rect.h);
    Rect {
        x: screen.rect.right() - w as i32,
        y: screen.rect.y + ((screen.rect.h - h) / 2) as i32,
        w,
        h,
    }
}

/// Horizontal distance from the right edge within which a drag wakes the strip.
pub fn approach_px(screen: &Screen) -> f64 {
    APPROACH_LOGICAL * screen.scale
}

pub fn edge_band_px(screen: &Screen) -> f64 {
    EDGE_BAND_LOGICAL * screen.scale
}

/// The vertical span of the strip, widened by 10% of the screen so a drag arriving slightly
/// above or below the strip still arms it.
pub fn strip_y_range(screen: &Screen) -> (f64, f64) {
    let r = layout(screen, Mode::Armed);
    let slack = screen.rect.h as f64 * 0.10;
    (r.y as f64 - slack, r.bottom() as f64 + slack)
}

/// A screen whose right edge is on the outside of the desktop (no monitor further right that
/// overlaps it vertically). The inner border between two monitors is not an edge for HandOff:
/// the cursor must be free to cross it.
pub fn is_outer_right(screens: &[Screen], index: usize) -> bool {
    let this = &screens[index].rect;
    !screens.iter().enumerate().any(|(i, other)| {
        i != index
            && other.rect.x >= this.right() - 1
            && other.rect.y < this.bottom()
            && other.rect.bottom() > this.y
    })
}

pub fn screen_at(screens: &[Screen], x: f64, y: f64) -> Option<usize> {
    screens.iter().position(|s| s.rect.contains(x, y))
}

/// Where the edge starts: the primary screen if its right edge is outer, else the rightmost
/// outer screen.
pub fn initial_screen(screens: &[Screen], primary: Option<usize>) -> Option<usize> {
    if let Some(p) = primary.filter(|&p| p < screens.len() && is_outer_right(screens, p)) {
        return Some(p);
    }
    (0..screens.len())
        .filter(|&i| is_outer_right(screens, i))
        .max_by_key(|&i| screens[i].rect.right())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn screen(x: i32, y: i32, w: u32, h: u32, scale: f64) -> Screen {
        Screen {
            rect: Rect { x, y, w, h },
            scale,
        }
    }

    #[test]
    fn the_strip_is_about_two_centimetres_at_the_css_reference() {
        let w = armed_width_logical();
        assert!((74.0..=78.0).contains(&w), "{w}");
    }

    #[test]
    fn the_strip_scales_with_the_display_and_stays_flush_right() {
        for scale in [1.0, 1.25, 1.5, 2.0] {
            let s = screen(
                0,
                0,
                (1920.0 * scale) as u32,
                (1080.0 * scale) as u32,
                scale,
            );
            let r = layout(&s, Mode::Armed);
            assert_eq!(r.right(), s.rect.right(), "flush right at {scale}x");
            let logical = r.w as f64 / scale;
            assert!(
                (logical - armed_width_logical()).abs() < 1.0,
                "{scale}x -> {logical}"
            );
            assert!(
                r.y > 0 && r.bottom() < s.rect.bottom(),
                "vertically centred"
            );
        }
    }

    #[test]
    fn layout_follows_the_monitor_offset() {
        let s = screen(1920, 200, 2560, 1440, 1.0);
        let r = layout(&s, Mode::Armed);
        assert_eq!(r.right(), 1920 + 2560);
        assert!(r.y > 200 && r.bottom() < 200 + 1440);
    }

    #[test]
    fn a_negative_origin_monitor_is_handled() {
        let s = screen(-1920, 0, 1920, 1080, 1.0);
        assert_eq!(layout(&s, Mode::Idle).right(), 0);
    }

    #[test]
    fn the_idle_strip_is_a_hairline_and_the_panel_is_the_widest_interactive_mode() {
        let s = screen(0, 0, 1920, 1080, 1.0);
        assert!(layout(&s, Mode::Idle).w <= 8);
        assert_eq!(layout(&s, Mode::Panel).w, 360);
        assert!(layout(&s, Mode::Panel).h <= 600);
    }

    #[test]
    fn nothing_is_ever_larger_than_the_screen() {
        let tiny = screen(0, 0, 300, 200, 1.0);
        for m in [Mode::Idle, Mode::Armed, Mode::Panel, Mode::Stage] {
            let r = layout(&tiny, m);
            assert!(
                r.w <= 300 && r.h <= 200 && r.h >= 1 && r.right() == 300,
                "{m:?} {r:?}"
            );
        }
    }

    #[test]
    fn only_the_armed_strip_and_the_panel_take_pointer_events() {
        assert!(Mode::Armed.hittable() && Mode::Panel.hittable());
        assert!(!Mode::Idle.hittable() && !Mode::Stage.hittable());
        assert!(Mode::Panel.focusable() && !Mode::Armed.focusable() && !Mode::Stage.focusable());
    }

    #[test]
    fn a_single_monitor_is_an_outer_right_edge() {
        assert!(is_outer_right(&[screen(0, 0, 1920, 1080, 1.0)], 0));
    }

    #[test]
    fn the_inner_border_between_two_side_by_side_monitors_is_not_an_edge() {
        let screens = [
            screen(0, 0, 1920, 1080, 1.0),
            screen(1920, 0, 1920, 1080, 1.0),
        ];
        assert!(!is_outer_right(&screens, 0));
        assert!(is_outer_right(&screens, 1));
    }

    #[test]
    fn a_monitor_to_the_left_does_not_hide_the_right_edge() {
        let screens = [
            screen(-1920, 0, 1920, 1080, 1.0),
            screen(0, 0, 1920, 1080, 1.0),
        ];
        assert!(!is_outer_right(&screens, 0) && is_outer_right(&screens, 1));
    }

    #[test]
    fn a_monitor_above_or_below_does_not_count_as_to_the_right() {
        let screens = [
            screen(0, 0, 1920, 1080, 1.0),
            screen(0, 1080, 1920, 1080, 1.0),
        ];
        assert!(is_outer_right(&screens, 0) && is_outer_right(&screens, 1));
    }

    #[test]
    fn a_staggered_monitor_that_does_not_overlap_vertically_is_not_to_the_right() {
        let screens = [
            screen(0, 0, 1920, 1080, 1.0),
            screen(1920, 1080, 1920, 1080, 1.0),
        ];
        assert!(is_outer_right(&screens, 0));
    }

    #[test]
    fn three_monitors_in_a_row_only_the_last_has_an_edge() {
        let screens = [
            screen(0, 0, 1920, 1080, 1.0),
            screen(1920, 0, 1920, 1080, 1.0),
            screen(3840, 0, 1920, 1080, 1.0),
        ];
        let outer: Vec<bool> = (0..3).map(|i| is_outer_right(&screens, i)).collect();
        assert_eq!(outer, [false, false, true]);
    }

    #[test]
    fn the_cursor_is_matched_to_its_monitor() {
        let screens = [
            screen(0, 0, 1920, 1080, 1.0),
            screen(1920, 0, 1280, 1024, 1.0),
        ];
        assert_eq!(screen_at(&screens, 10.0, 10.0), Some(0));
        assert_eq!(screen_at(&screens, 2000.0, 10.0), Some(1));
        assert_eq!(screen_at(&screens, 2000.0, 1050.0), None);
    }

    #[test]
    fn the_edge_starts_on_the_primary_monitor_when_it_has_an_outer_edge() {
        let screens = [
            screen(0, 0, 1920, 1080, 1.0),
            screen(1920, 0, 1920, 1080, 1.0),
        ];
        assert_eq!(initial_screen(&screens, Some(1)), Some(1));
        // primary is the inner monitor: fall back to the rightmost outer one
        assert_eq!(initial_screen(&screens, Some(0)), Some(1));
        assert_eq!(initial_screen(&screens, None), Some(1));
        assert_eq!(initial_screen(&[], None), None);
    }
}
