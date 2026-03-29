import { ThemeId } from '../types';
import { CanvasViewport, SceneModel } from './helpers';

export interface ThemeDrawParams {
  ctx: CanvasRenderingContext2D;
  physicalViewport: CanvasViewport;
  viewport: CanvasViewport;
  scene: SceneModel;
  liteMode: boolean;
  runtime: Record<string, unknown>;
  drawUprightText: (text: string, x: number, y: number, maxWidth?: number) => void;
}

export interface ViewerTheme {
  id: ThemeId;
  label: string;
  summary: string;
  dprCap: number;
  createRuntimeState: () => Record<string, unknown>;
  draw: (params: ThemeDrawParams) => void;
}
