import { ThemeId } from '../types';
import { minimalTheme } from './minimalTheme';
import { neonTheme } from './neonTheme';
import { retroTheme } from './retroTheme';
import { oceanicTheme } from './oceanicTheme';
import { terminalTheme } from './terminalTheme';
import { sunsetTheme } from './sunsetTheme';
import { chalkboardTheme } from './chalkboardTheme';
import { blueprintTheme } from './blueprintTheme';
import { holographicTheme } from './holographicTheme';
import { botanicalTheme } from './botanicalTheme';
import { ViewerTheme } from './themeTypes';

const THEMES: Record<ThemeId, ViewerTheme> = {
  neon: neonTheme,
  retro: retroTheme,
  minimal: minimalTheme,
  oceanic: oceanicTheme,
  terminal: terminalTheme,
  sunset: sunsetTheme,
  chalkboard: chalkboardTheme,
  blueprint: blueprintTheme,
  holographic: holographicTheme,
  botanical: botanicalTheme,
};

export const THEME_ORDER: ThemeId[] = ['neon', 'retro', 'minimal', 'oceanic', 'terminal', 'sunset', 'chalkboard', 'blueprint', 'holographic', 'botanical'];

export function themeById(id: ThemeId): ViewerTheme {
  return THEMES[id];
}

export function isKnownTheme(value: string): value is ThemeId {
  return value in THEMES;
}

export function listThemeIds(): ThemeId[] {
  return [...THEME_ORDER];
}
