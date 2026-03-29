import { describe, expect, it } from 'vitest';

import { ViewerTimelineBar, ViewerTimelineFrameV1, ViewerTimelineV1 } from '../types';
import { buildSceneModel } from './helpers';

const VIEWPORT = { cssWidth: 1200, cssHeight: 700, dpr: 1 };

function bar(overrides: Partial<ViewerTimelineBar> = {}): ViewerTimelineBar {
  return {
    id: 1,
    pitch: 60,
    start_us: 0,
    end_us: 100_000,
    velocity: 96,
    frequency_hz: 261.6,
    channel: 1,
    motor_slot: 0,
    ...overrides,
  };
}

function timeline(frames: ViewerTimelineFrameV1[], bars: ViewerTimelineBar[]): ViewerTimelineV1 {
  return {
    version: 'v1',
    fps: 30,
    duration_us: 2_000_000,
    frame_count: frames.length,
    note_range: { min_note: 36, max_note: 96 },
    bars_static: bars,
    frames,
    style_hints: {},
    generated_at_unix_ms: 0,
  };
}

describe('scene model', () => {
  it('filters tiny ghost bars from visible rendering lists', () => {
    const shortBar = bar({ id: 1, pitch: 60, start_us: 100_000, end_us: 102_500 });
    const longBar = bar({ id: 2, pitch: 64, start_us: 100_000, end_us: 260_000 });
    const frame: ViewerTimelineFrameV1 = {
      playhead_us: 150_000,
      window_start_us: 0,
      window_end_us: 400_000,
      active_note_ids: [1, 2],
      visible_bar_ids: [1, 2],
      beat_markers_us: [],
    };

    const scene = buildSceneModel({
      session: null,
      timeline: timeline([frame], [shortBar, longBar]),
      frame,
      colorMode: 'channel',
      smoothPlayheadUs: 150_000,
      barsById: new Map<number, ViewerTimelineBar>([
        [1, shortBar],
        [2, longBar],
      ]),
      viewport: VIEWPORT,
    });

    expect(scene.visibleBars.map((candidate) => candidate.id)).toEqual([2]);
    expect(scene.activePitchColors.has(64)).toBe(true);
    expect(scene.activePitchColors.has(60)).toBe(false);
  });

  it('does not synthesize active colors from filtered ghost bars', () => {
    const shortBar = bar({ id: 9, pitch: 72, start_us: 500_000, end_us: 502_000 });
    const frame: ViewerTimelineFrameV1 = {
      playhead_us: 501_000,
      window_start_us: 300_000,
      window_end_us: 700_000,
      active_note_ids: [9],
      visible_bar_ids: [9],
      beat_markers_us: [],
    };

    const scene = buildSceneModel({
      session: null,
      timeline: timeline([frame], [shortBar]),
      frame,
      colorMode: 'frequency_bands',
      smoothPlayheadUs: 501_000,
      barsById: new Map<number, ViewerTimelineBar>([[9, shortBar]]),
      viewport: VIEWPORT,
    });

    expect(scene.visibleBars).toEqual([]);
    expect(scene.activePitchColors.size).toBe(0);
  });
});
