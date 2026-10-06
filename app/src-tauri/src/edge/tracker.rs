//! Decides when the edge wakes up. Pure: it is fed cursor samples and returns actions, so every
//! rule is unit-tested without a display or a real mouse.
//!
//! Rules:
//! * Dragging (button held) near an *outer* right edge arms the strip, so it is already
//!   hittable when the OS drag reaches it. Only a held button arms it: merely moving the
//!   pointer to the edge never covers scrollbars or window buttons.
//! * Resting the pointer in the outermost pixels for a moment arms it as a click target for
//!   the control panel (the "handle").
//! * Once armed it stays armed while the pointer is over it, so a drop that is being delivered
//!   is never cut off by the button being released.
//! * Panel and Stage belong to the UI; the tracker stays silent while they are active.

use std::time::{Duration, Instant};

use serde::Serialize;

use super::geometry::{
    approach_px, edge_band_px, is_outer_right, layout, screen_at, strip_y_range, Mode, Screen,
};

/// How long the pointer must rest on the edge before the handle appears.
pub const DWELL: Duration = Duration::from_millis(600);

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    Far,
    Near,
    Drag,
    Dwell,
}

#[derive(Debug, PartialEq)]
pub enum Action {
    SetMode { mode: Mode, screen: usize },
    Proximity(Phase),
}

pub struct Sample<'a> {
    pub cursor: (f64, f64),
    pub screens: &'a [Screen],
    pub pressed: bool,
    pub now: Instant,
}

pub struct Tracker {
    pub mode: Mode,
    pub screen: Option<usize>,
    phase: Phase,
    dwell_since: Option<Instant>,
}

impl Tracker {
    pub fn new(screen: Option<usize>) -> Self {
        Self {
            mode: Mode::Idle,
            screen,
            phase: Phase::Far,
            dwell_since: None,
        }
    }

    /// The UI changed the mode (panel opened, animation started or finished).
    pub fn external_mode(&mut self, mode: Mode, screen: usize) {
        self.mode = mode;
        self.screen = Some(screen);
        self.dwell_since = None;
        if matches!(mode, Mode::Idle) {
            self.phase = Phase::Far;
        }
    }

    pub fn is_calm(&self) -> bool {
        self.mode == Mode::Idle && self.phase == Phase::Far
    }

    pub fn step(&mut self, s: &Sample) -> Vec<Action> {
        if matches!(self.mode, Mode::Panel | Mode::Stage) {
            self.dwell_since = None;
            return vec![];
        }
        let idx =
            screen_at(s.screens, s.cursor.0, s.cursor.1).filter(|&i| is_outer_right(s.screens, i));
        let (mut near, mut band, mut in_window) = (false, false, false);
        if let Some(i) = idx {
            let scr = &s.screens[i];
            let (y0, y1) = strip_y_range(scr);
            let vertical = s.cursor.1 >= y0 && s.cursor.1 < y1;
            let right = scr.rect.right() as f64;
            near = vertical && s.cursor.0 >= right - approach_px(scr);
            band = vertical && s.cursor.0 >= right - edge_band_px(scr);
            in_window = self.mode == Mode::Armed
                && self.screen == Some(i)
                && layout(scr, Mode::Armed).contains(s.cursor.0, s.cursor.1);
        }

        let phase = if s.pressed && near {
            self.dwell_since = None;
            Phase::Drag
        } else if !s.pressed && band {
            let since = *self.dwell_since.get_or_insert(s.now);
            if s.now.duration_since(since) >= DWELL {
                Phase::Dwell
            } else {
                Phase::Near
            }
        } else {
            self.dwell_since = None;
            if near {
                Phase::Near
            } else {
                Phase::Far
            }
        };

        let want_armed = matches!(phase, Phase::Drag | Phase::Dwell) || in_window;
        let mut actions = Vec::new();
        if want_armed {
            // Arm (or move the strip to the screen the drag is on). `idx` is always set when the
            // phase is Drag/Dwell; `in_window` keeps an already-armed strip where it is.
            if let Some(screen) = idx.filter(|&i| self.mode == Mode::Idle || self.screen != Some(i))
            {
                if !in_window {
                    actions.push(Action::SetMode {
                        mode: Mode::Armed,
                        screen,
                    });
                    self.mode = Mode::Armed;
                    self.screen = Some(screen);
                }
            }
        } else if self.mode == Mode::Armed {
            actions.push(Action::SetMode {
                mode: Mode::Idle,
                screen: self.screen.unwrap_or(0),
            });
            self.mode = Mode::Idle;
        }
        if phase != self.phase {
            self.phase = phase;
            actions.push(Action::Proximity(phase));
        }
        actions
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::edge::geometry::Rect;

    fn one() -> Vec<Screen> {
        vec![Screen {
            rect: Rect {
                x: 0,
                y: 0,
                w: 1920,
                h: 1080,
            },
            scale: 1.0,
        }]
    }
    fn two() -> Vec<Screen> {
        vec![
            Screen {
                rect: Rect {
                    x: 0,
                    y: 0,
                    w: 1920,
                    h: 1080,
                },
                scale: 1.0,
            },
            Screen {
                rect: Rect {
                    x: 1920,
                    y: 0,
                    w: 1920,
                    h: 1080,
                },
                scale: 1.0,
            },
        ]
    }
    fn sample<'a>(
        screens: &'a [Screen],
        x: f64,
        y: f64,
        pressed: bool,
        now: Instant,
    ) -> Sample<'a> {
        Sample {
            cursor: (x, y),
            screens,
            pressed,
            now,
        }
    }
    fn has_mode(a: &[Action], m: Mode) -> bool {
        a.iter()
            .any(|x| matches!(x, Action::SetMode { mode, .. } if *mode == m))
    }

    #[test]
    fn moving_the_pointer_to_the_edge_without_a_button_never_arms_the_strip() {
        let (screens, t0) = (one(), Instant::now());
        let mut t = Tracker::new(Some(0));
        for ms in [0u64, 100, 200, 300] {
            let a = t.step(&sample(
                &screens,
                1880.0,
                540.0,
                false,
                t0 + Duration::from_millis(ms),
            ));
            assert!(!has_mode(&a, Mode::Armed), "armed at {ms} ms");
        }
        assert_eq!(t.mode, Mode::Idle);
    }

    #[test]
    fn dragging_near_the_edge_arms_the_strip_before_the_pointer_arrives() {
        let (screens, now) = (one(), Instant::now());
        let mut t = Tracker::new(Some(0));
        let a = t.step(&sample(&screens, 1800.0, 540.0, true, now)); // ~120 px away
        assert!(has_mode(&a, Mode::Armed));
        assert!(a.contains(&Action::Proximity(Phase::Drag)));
        assert_eq!(t.mode, Mode::Armed);
    }

    #[test]
    fn a_drag_far_from_the_edge_does_nothing() {
        let (screens, now) = (one(), Instant::now());
        let mut t = Tracker::new(Some(0));
        assert!(t
            .step(&sample(&screens, 900.0, 540.0, true, now))
            .is_empty());
        assert!(t.is_calm());
    }

    #[test]
    fn a_drag_near_the_top_corner_does_not_arm_the_centred_strip() {
        let (screens, now) = (one(), Instant::now());
        let mut t = Tracker::new(Some(0));
        let a = t.step(&sample(&screens, 1915.0, 10.0, true, now));
        assert!(!has_mode(&a, Mode::Armed));
    }

    #[test]
    fn dragging_away_disarms_the_strip() {
        let (screens, now) = (one(), Instant::now());
        let mut t = Tracker::new(Some(0));
        t.step(&sample(&screens, 1800.0, 540.0, true, now));
        let a = t.step(&sample(&screens, 1000.0, 540.0, true, now));
        assert!(has_mode(&a, Mode::Idle) && a.contains(&Action::Proximity(Phase::Far)));
    }

    #[test]
    fn releasing_the_button_over_the_strip_keeps_it_armed_so_the_drop_lands() {
        let (screens, now) = (one(), Instant::now());
        let mut t = Tracker::new(Some(0));
        t.step(&sample(&screens, 1800.0, 540.0, true, now));
        let a = t.step(&sample(&screens, 1900.0, 540.0, false, now)); // inside the 76 px strip
        assert!(!has_mode(&a, Mode::Idle));
        assert_eq!(t.mode, Mode::Armed);
    }

    #[test]
    fn releasing_the_button_away_from_the_strip_disarms_it() {
        let (screens, now) = (one(), Instant::now());
        let mut t = Tracker::new(Some(0));
        t.step(&sample(&screens, 1800.0, 540.0, true, now));
        let a = t.step(&sample(&screens, 1810.0, 540.0, false, now)); // near but not over it
        assert!(has_mode(&a, Mode::Idle));
    }

    #[test]
    fn resting_on_the_outermost_pixels_shows_the_handle_after_the_dwell() {
        let (screens, t0) = (one(), Instant::now());
        let mut t = Tracker::new(Some(0));
        let early = t.step(&sample(&screens, 1918.0, 540.0, false, t0));
        assert!(!has_mode(&early, Mode::Armed));
        let still = t.step(&sample(
            &screens,
            1918.0,
            540.0,
            false,
            t0 + Duration::from_millis(500),
        ));
        assert!(!has_mode(&still, Mode::Armed));
        let a = t.step(&sample(
            &screens,
            1918.0,
            540.0,
            false,
            t0 + Duration::from_millis(650),
        ));
        assert!(has_mode(&a, Mode::Armed) && a.contains(&Action::Proximity(Phase::Dwell)));
    }

    #[test]
    fn leaving_the_edge_before_the_dwell_resets_it() {
        let (screens, t0) = (one(), Instant::now());
        let mut t = Tracker::new(Some(0));
        t.step(&sample(&screens, 1918.0, 540.0, false, t0));
        t.step(&sample(
            &screens,
            1500.0,
            540.0,
            false,
            t0 + Duration::from_millis(400),
        ));
        let a = t.step(&sample(
            &screens,
            1918.0,
            540.0,
            false,
            t0 + Duration::from_millis(700),
        ));
        assert!(!has_mode(&a, Mode::Armed), "the clock restarted");
    }

    #[test]
    fn the_inner_border_between_monitors_never_arms_anything() {
        let (screens, now) = (two(), Instant::now());
        let mut t = Tracker::new(Some(1));
        // dragging across the seam of monitor 0 onto monitor 1
        for x in [1800.0, 1900.0, 1919.0] {
            let a = t.step(&sample(&screens, x, 540.0, true, now));
            assert!(!has_mode(&a, Mode::Armed), "armed at x={x}");
        }
    }

    #[test]
    fn the_outer_edge_of_the_second_monitor_arms_it_there() {
        let (screens, now) = (two(), Instant::now());
        let mut t = Tracker::new(Some(1));
        let a = t.step(&sample(&screens, 3800.0, 540.0, true, now));
        assert!(a.contains(&Action::SetMode {
            mode: Mode::Armed,
            screen: 1
        }));
    }

    #[test]
    fn the_strip_follows_a_drag_to_another_outer_screen() {
        // two stacked monitors, both with an outer right edge
        let screens = vec![
            Screen {
                rect: Rect {
                    x: 0,
                    y: 0,
                    w: 1920,
                    h: 1080,
                },
                scale: 1.0,
            },
            Screen {
                rect: Rect {
                    x: 0,
                    y: 1080,
                    w: 1920,
                    h: 1080,
                },
                scale: 1.0,
            },
        ];
        let now = Instant::now();
        let mut t = Tracker::new(Some(0));
        t.step(&sample(&screens, 1800.0, 540.0, true, now));
        let a = t.step(&sample(&screens, 1800.0, 1620.0, true, now));
        assert!(a.contains(&Action::SetMode {
            mode: Mode::Armed,
            screen: 1
        }));
    }

    #[test]
    fn proximity_is_reported_only_when_it_changes() {
        let (screens, now) = (one(), Instant::now());
        let mut t = Tracker::new(Some(0));
        t.step(&sample(&screens, 1800.0, 540.0, true, now));
        let again = t.step(&sample(&screens, 1810.0, 540.0, true, now));
        assert!(again.is_empty(), "{again:?}");
    }

    #[test]
    fn while_the_panel_or_an_animation_is_showing_the_tracker_stays_out_of_the_way() {
        let (screens, now) = (one(), Instant::now());
        for mode in [Mode::Panel, Mode::Stage] {
            let mut t = Tracker::new(Some(0));
            t.external_mode(mode, 0);
            assert!(t
                .step(&sample(&screens, 1800.0, 540.0, true, now))
                .is_empty());
            assert_eq!(t.mode, mode);
        }
    }

    #[test]
    fn after_the_ui_returns_to_idle_dragging_arms_again() {
        let (screens, now) = (one(), Instant::now());
        let mut t = Tracker::new(Some(0));
        t.external_mode(Mode::Stage, 0);
        t.external_mode(Mode::Idle, 0);
        assert!(t.is_calm());
        assert!(has_mode(
            &t.step(&sample(&screens, 1800.0, 540.0, true, now)),
            Mode::Armed
        ));
    }

    #[test]
    fn a_hidpi_screen_uses_a_proportionally_larger_approach_zone() {
        let screens = vec![Screen {
            rect: Rect {
                x: 0,
                y: 0,
                w: 3840,
                h: 2160,
            },
            scale: 2.0,
        }];
        let now = Instant::now();
        let mut t = Tracker::new(Some(0));
        // 200 physical px away is 100 logical px: inside the 140 logical px zone
        assert!(has_mode(
            &t.step(&sample(&screens, 3640.0, 1080.0, true, now)),
            Mode::Armed
        ));
        let mut far = Tracker::new(Some(0));
        // 400 physical px away is 200 logical px: outside
        assert!(far
            .step(&sample(&screens, 3440.0, 1080.0, true, now))
            .is_empty());
    }

    #[test]
    fn a_cursor_outside_every_monitor_is_ignored() {
        let (screens, now) = (one(), Instant::now());
        let mut t = Tracker::new(Some(0));
        assert!(t
            .step(&sample(&screens, -50.0, 540.0, true, now))
            .is_empty());
    }
}
