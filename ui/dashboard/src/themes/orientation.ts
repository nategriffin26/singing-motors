import { CanvasViewport } from './helpers';

export interface ScenePoint {
  x: number;
  y: number;
}

export function toLogicalViewport(viewport: CanvasViewport): CanvasViewport {
  return {
    cssWidth: viewport.cssHeight,
    cssHeight: viewport.cssWidth,
    dpr: viewport.dpr,
  };
}

export function logicalToPhysicalPoint(
  point: ScenePoint,
  viewport: CanvasViewport,
): ScenePoint {
  return {
    x: viewport.cssWidth - point.y,
    y: viewport.cssHeight - point.x,
  };
}

export function applyVerticalSceneTransform(
  ctx: CanvasRenderingContext2D,
  viewport: CanvasViewport,
): void {
  // Rotate the scene 90 degrees counterclockwise with translation into view.
  ctx.transform(0, -1, -1, 0, viewport.cssWidth, viewport.cssHeight);
}

export function createUprightTextDrawer(
  ctx: CanvasRenderingContext2D,
  viewport: CanvasViewport,
): (text: string, x: number, y: number, maxWidth?: number) => void {
  return (text: string, x: number, y: number, maxWidth?: number): void => {
    const mapped = logicalToPhysicalPoint({ x, y }, viewport);
    ctx.save();
    ctx.setTransform(viewport.dpr, 0, 0, viewport.dpr, 0, 0);
    if (maxWidth === undefined) {
      ctx.fillText(text, mapped.x, mapped.y);
    } else {
      ctx.fillText(text, mapped.x, mapped.y, maxWidth);
    }
    ctx.restore();
  };
}
