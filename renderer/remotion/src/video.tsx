import React from 'react';
import {AbsoluteFill, staticFile} from 'remotion';
import {Audio} from '@remotion/media';
import {linearTiming, TransitionSeries} from '@remotion/transitions';
import {fade} from '@remotion/transitions/fade';
import type {RenderProps} from './types';
import {LemoWatercolorScene} from './lemo-watercolor';
import {LEMO_SCENE_STYLES, LemoStyleScene} from './lemo-style-scene';
import {VisualScene} from './visuals';

const TRANSITION_FRAMES = 12;

export const OlyphantsComposition: React.FC<RenderProps> = ({audioFile, scenes, visualStyle = 'remotion'}) => {
  const parts: React.ReactNode[] = [];
  scenes.forEach((scene, index) => {
    const sceneVisualStyle = scene.visualStyle || visualStyle;
    if (index > 0 && scene.transition !== 'cut') {
      parts.push(<TransitionSeries.Transition key={`transition-${index}`} timing={linearTiming({durationInFrames: TRANSITION_FRAMES})} presentation={fade()} />);
    }
    parts.push(
      <TransitionSeries.Sequence key={`scene-${index}`} durationInFrames={scene.durationInFrames}>
        {sceneVisualStyle === 'lemo-watercolor' || sceneVisualStyle === 'watercolor'
          ? <LemoWatercolorScene scene={scene} index={index} />
          : LEMO_SCENE_STYLES.includes(sceneVisualStyle as (typeof LEMO_SCENE_STYLES)[number])
            ? <LemoStyleScene scene={scene} index={index} style={sceneVisualStyle as (typeof LEMO_SCENE_STYLES)[number]} />
            : <VisualScene scene={scene} index={index} />}
      </TransitionSeries.Sequence>,
    );
  });
  return <AbsoluteFill style={{backgroundColor: '#061A2B'}}>
    <TransitionSeries>{parts}</TransitionSeries>
    <Audio src={staticFile(audioFile)} />
  </AbsoluteFill>;
};
