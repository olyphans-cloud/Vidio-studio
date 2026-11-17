export type VisualType =
  | 'kinetic'
  | 'explainer'
  | 'infographic'
  | 'chart'
  | 'map'
  | 'illustration'
  | 'ui'
  | 'media'
  | 'hybrid';

export type RenderScene = {
  narration: string;
  heading: string;
  visualPrompt: string;
  visualType: VisualType;
  visualMode: 'generated' | 'media' | 'hybrid';
  visualStyle?: string;
  illustrativeOnly?: boolean;
  transition?: 'fade' | 'wipe' | 'slide' | 'cut';
  durationInFrames: number;
  mediaFile?: string;
  mediaFiles?: Array<{file: string; durationInFrames: number}>;
};

export type RenderProps = {
  title: string;
  audioFile: string;
  scenes: RenderScene[];
  fps: number;
  durationInFrames: number;
  width: number;
  height: number;
  visualStyle?: string;
};
