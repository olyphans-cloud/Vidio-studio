import React from 'react';
import {Composition} from 'remotion';
import {OlyphantsComposition} from './video';
import type {RenderProps} from './types';

const defaults: RenderProps = {
  title: 'Olyphants Video',
  audioFile: '',
  scenes: [],
  fps: 30,
  durationInFrames: 600,
  width: 1080,
  height: 1920,
};

export const RemotionRoot: React.FC = () => (
  <Composition
    id="OlyphantsVideo"
    component={OlyphantsComposition}
    defaultProps={defaults}
    durationInFrames={defaults.durationInFrames}
    fps={defaults.fps}
    width={defaults.width}
    height={defaults.height}
    calculateMetadata={({props}) => ({
      durationInFrames: Math.max(1, props.durationInFrames),
      fps: props.fps,
      width: props.width,
      height: props.height,
    })}
  />
);
