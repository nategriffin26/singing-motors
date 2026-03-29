import { describe, expect, it } from 'vitest';

import { ViewerTimelineBar } from '../types';
import { listColorModeIds, resolveBarColor } from './helpers';

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

describe('color mode resolver', () => {
  it('exposes all expected color modes in stable order', () => {
    expect(listColorModeIds()).toEqual([
      'monochrome_accent',
      'channel',
      'octave_bands',
      'frequency_bands',
      'motor_slot',
      'velocity_intensity',
    ]);
  });

  it('uses clearer accent split for monochrome mode', () => {
    const b = bar();
    const inactive = resolveBarColor(b, 'monochrome_accent', { active: false });
    const active = resolveBarColor(b, 'monochrome_accent', { active: true });
    expect(inactive).toBe('#748290');
    expect(active).toBe('#59b8dc');
  });

  it('maps channel mode by channel index and wraps safely', () => {
    const c1 = resolveBarColor(bar({ channel: 1 }), 'channel', { active: false });
    const c17 = resolveBarColor(bar({ channel: 17 }), 'channel', { active: false });
    expect(c1).toBe(c17);
  });

  it('maps octave bands with coarse low/high separation', () => {
    const low = resolveBarColor(bar({ pitch: 36 }), 'octave_bands', { active: false });
    const high = resolveBarColor(bar({ pitch: 96 }), 'octave_bands', { active: false });
    expect(low).not.toBe(high);
  });

  it('maps frequency bands using provided frequency_hz', () => {
    const low = resolveBarColor(bar({ frequency_hz: 40 }), 'frequency_bands', { active: false });
    const high = resolveBarColor(bar({ frequency_hz: 520 }), 'frequency_bands', { active: false });
    expect(low).not.toBe(high);
  });

  it('falls back to pitch-derived frequency when frequency_hz is missing', () => {
    const low = resolveBarColor(bar({ pitch: 40, frequency_hz: undefined }), 'frequency_bands', { active: false });
    const high = resolveBarColor(bar({ pitch: 88, frequency_hz: undefined }), 'frequency_bands', { active: false });
    expect(low).not.toBe(high);
  });

  it('maps motor slots to distinct palette entries', () => {
    const a = resolveBarColor(bar({ motor_slot: 0 }), 'motor_slot', { active: false });
    const b = resolveBarColor(bar({ motor_slot: 3 }), 'motor_slot', { active: false });
    expect(a).not.toBe(b);
  });

  it('maps velocity intensity and brightens active notes', () => {
    const quiet = resolveBarColor(bar({ velocity: 20 }), 'velocity_intensity', { active: false });
    const loud = resolveBarColor(bar({ velocity: 120 }), 'velocity_intensity', { active: false });
    const loudActive = resolveBarColor(bar({ velocity: 120 }), 'velocity_intensity', { active: true });
    expect(quiet).not.toBe(loud);
    expect(loudActive).not.toBe(loud);
  });
});
