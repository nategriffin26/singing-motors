import { describe, expect, it } from 'vitest';

import { logicalToPhysicalPoint, toLogicalViewport } from './orientation';

describe('vertical orientation mapping', () => {
  const physicalViewport = {
    cssWidth: 1200,
    cssHeight: 700,
    dpr: 2,
  };

  it('swaps logical viewport dimensions', () => {
    const logical = toLogicalViewport(physicalViewport);
    expect(logical.cssWidth).toBe(700);
    expect(logical.cssHeight).toBe(1200);
    expect(logical.dpr).toBe(2);
  });

  it('maps keyboard edge near the physical bottom', () => {
    const point = logicalToPhysicalPoint({ x: 24, y: 500 }, physicalViewport);
    expect(point.y).toBe(676);
  });

  it('maps higher pitches to the right and lower pitches to the left', () => {
    const highPitch = logicalToPhysicalPoint({ x: 160, y: 120 }, physicalViewport);
    const lowPitch = logicalToPhysicalPoint({ x: 160, y: 500 }, physicalViewport);

    expect(highPitch.x).toBeGreaterThan(lowPitch.x);
  });

  it('maps future notes above the playhead so they fall downward over time', () => {
    const playhead = logicalToPhysicalPoint({ x: 160, y: 360 }, physicalViewport);
    const futureNote = logicalToPhysicalPoint({ x: 380, y: 360 }, physicalViewport);

    expect(futureNote.y).toBeLessThan(playhead.y);
  });

  it('keeps mapped points inside physical bounds', () => {
    const logical = toLogicalViewport(physicalViewport);
    const corners = [
      { x: 0, y: 0 },
      { x: logical.cssWidth, y: 0 },
      { x: 0, y: logical.cssHeight },
      { x: logical.cssWidth, y: logical.cssHeight },
    ];

    for (const corner of corners) {
      const mapped = logicalToPhysicalPoint(corner, physicalViewport);
      expect(mapped.x).toBeGreaterThanOrEqual(0);
      expect(mapped.x).toBeLessThanOrEqual(physicalViewport.cssWidth);
      expect(mapped.y).toBeGreaterThanOrEqual(0);
      expect(mapped.y).toBeLessThanOrEqual(physicalViewport.cssHeight);
    }
  });
});
