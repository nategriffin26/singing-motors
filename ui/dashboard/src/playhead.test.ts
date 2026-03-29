import { describe, expect, it } from 'vitest';

import {
  hasPlaybackStarted,
  shouldPollForPlaybackStart,
  START_DETECT_MIN_PLAYHEAD_US,
  START_DETECT_POLL_MS,
  toViewerPlayheadUs,
} from './playhead';

describe('playhead startup sync helpers', () => {
  it('keeps polling only until playback clock is latched', () => {
    expect(shouldPollForPlaybackStart(null)).toBe(true);
    expect(shouldPollForPlaybackStart(1234.5)).toBe(false);
  });

  it('uses fast startup polling to avoid large first-frame snaps', () => {
    expect(START_DETECT_POLL_MS).toBeLessThanOrEqual(20);
  });

  it('converts motor playhead to viewer playhead with sync offset applied', () => {
    expect(toViewerPlayheadUs(0, 0)).toBe(0);
    expect(toViewerPlayheadUs(250_000, 0)).toBe(250_000);
    expect(toViewerPlayheadUs(250_000, 75)).toBe(175_000);
    expect(toViewerPlayheadUs(50_000, 100)).toBe(0);
  });

  it('waits for a stable non-trivial playhead before latching playback start', () => {
    expect(hasPlaybackStarted(0)).toBe(false);
    expect(hasPlaybackStarted(START_DETECT_MIN_PLAYHEAD_US - 1)).toBe(false);
    expect(hasPlaybackStarted(START_DETECT_MIN_PLAYHEAD_US)).toBe(true);
    expect(hasPlaybackStarted(55_000)).toBe(true);
  });
});
