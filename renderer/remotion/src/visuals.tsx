import React from 'react';
import {AbsoluteFill, Img, OffthreadVideo, Sequence, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import type {RenderScene} from './types';

const colors = ['#55D6BE', '#FFB86B', '#8AA7FF', '#F47DA3'];

const GeneratedArt: React.FC<{scene: RenderScene; index: number}> = ({scene, index}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const enter = spring({frame, fps, config: {damping: 18, mass: 0.8}});
  const turn = interpolate(frame, [0, 120], [-12, 12], {extrapolateRight: 'extend'});
  const pulse = 1 + Math.sin(frame / 9) * 0.055;
  const accent = colors[index % colors.length];
  const titleStyle: React.CSSProperties = {
    fontSize: 72,
    fontWeight: 850,
    lineHeight: 1.04,
    letterSpacing: '-0.04em',
    textAlign: 'left',
    maxWidth: 850,
    margin: 0,
    color: '#F7F8FC',
    opacity: enter,
    transform: `translateY(${(1 - enter) * 120}px)`,
    textShadow: '0 9px 35px #030716a8',
  };

  return (
    <AbsoluteFill style={{justifyContent: 'center', padding: 86, overflow: 'hidden'}}>
      <div style={{position: 'absolute', width: 780, height: 780, borderRadius: '50%', right: -270, top: 120, background: `radial-gradient(circle, ${accent}44, transparent 68%)`, transform: `scale(${pulse}) rotate(${turn}deg)`}} />
      <div style={{position: 'absolute', width: 620, height: 620, border: `2px solid ${accent}66`, borderRadius: scene.visualType === 'map' ? '38% 62% 58% 42%' : '50%', right: -80, top: 310, transform: `rotate(${turn * 2}deg) scale(${pulse})`}} />
      <ArtShape scene={scene} accent={accent} frame={frame} />
      <div style={{position: 'relative', zIndex: 2, width: '100%'}}>
        <div style={{width: 92, height: 8, borderRadius: 8, background: accent, marginBottom: 32, transform: `scaleX(${enter})`, transformOrigin: 'left'}} />
        <h1 style={titleStyle}>{scene.heading}</h1>
        {scene.visualType === 'chart' || scene.visualType === 'infographic' ? <AnimatedBars accent={accent} frame={frame} /> : null}
        {scene.visualType === 'ui' ? <SimulatedUi accent={accent} frame={frame} /> : null}
      </div>
      {scene.illustrativeOnly ? <IllustrationBadge /> : null}
      <div style={{position: 'absolute', bottom: 64, left: 86, right: 86, height: 4, background: '#ffffff24'}}>
        <div style={{height: '100%', width: `${interpolate(frame, [0, 40], [0, 100], {extrapolateRight: 'clamp'})}%`, background: accent}} />
      </div>
    </AbsoluteFill>
  );
};

const ArtShape: React.FC<{scene: RenderScene; accent: string; frame: number}> = ({scene, accent, frame}) => {
  const bob = Math.sin(frame / 8) * 18;
  if (scene.visualType === 'map') {
    return <svg viewBox="0 0 600 600" style={{position: 'absolute', width: 680, right: -70, top: 290, opacity: .8}}>
      <path d="M105 119l54-52 52 24 45-33 52 41 61-4 35 51 56 19-9 62 42 46-24 51 5 70-61 34-42 61-63-27-56 27-42-53-59-9-14-61-50-37 21-55-23-48 20-52-8-55z" fill={`${accent}1f`} stroke={accent} strokeWidth="5" />
      <path d={`M175 390 Q270 ${300 + bob} 395 220`} fill="none" stroke="#fff" strokeWidth="4" strokeDasharray="10 15" />
      <circle cx="175" cy="390" r="15" fill="#fff" /><circle cx="395" cy="220" r="21" fill={accent} />
    </svg>;
  }
  if (scene.visualType === 'illustration' || scene.visualType === 'explainer') {
    return <svg viewBox="0 0 500 500" style={{position: 'absolute', width: 510, right: -20, top: 330, transform: `translateY(${bob}px) rotate(${Math.sin(frame / 17) * 5}deg)`}}>
      <circle cx="250" cy="250" r="174" fill={`${accent}25`} stroke={`${accent}95`} strokeWidth="4" />
      <rect x="205" y="155" width="90" height="190" rx="38" fill={accent} transform={`rotate(${Math.sin(frame / 13) * 12} 250 250)`} />
      <circle cx="250" cy="205" r="32" fill="#07192a" /><path d="M172 360q78-95 156 0" fill="none" stroke="#fff" strokeWidth="18" strokeLinecap="round" />
    </svg>;
  }
  if (scene.visualType === 'ui') return null;
  return <div style={{position: 'absolute', right: 45, top: 420, width: 300, height: 300, borderRadius: scene.visualType === 'kinetic' ? 40 : '50%', border: `3px solid ${accent}8a`, boxShadow: `0 0 90px ${accent}32`, transform: `translateY(${bob}px) rotate(${frame * .7}deg)`}} />;
};

const AnimatedBars: React.FC<{accent: string; frame: number}> = ({accent, frame}) => (
  <div style={{display: 'flex', alignItems: 'end', gap: 16, height: 170, marginTop: 56}}>
    {[.46, .78, .59, .95, .72].map((value, index) => {
      const height = value * interpolate(frame, [0, 26], [0, 145], {extrapolateRight: 'clamp'});
      return <div key={index} style={{width: 66, height, borderRadius: '14px 14px 4px 4px', background: index === 4 ? '#fff' : accent, opacity: .72 + index * .05}} />;
    })}
  </div>
);

const SimulatedUi: React.FC<{accent: string; frame: number}> = ({accent, frame}) => (
  <div style={{position: 'absolute', right: 10, top: 110, width: 400, height: 740, padding: 24, borderRadius: 42, border: '4px solid #ffffffaa', background: '#10243df2', boxShadow: '0 25px 80px #0009', transform: `translateY(${Math.sin(frame / 15) * 10}px) rotate(5deg)`}}>
    <div style={{height: 18, width: 120, borderRadius: 12, background: '#ffffff55', margin: '0 auto 54px'}} />
    <div style={{fontSize: 22, color: accent, fontWeight: 800, marginBottom: 16}}>SIMULASI</div>
    {[88, 62, 76, 49].map((width, i) => <div key={i} style={{height: 38, width: `${width}%`, borderRadius: 10, background: i === 2 ? accent : '#ffffff24', margin: '22px 0'}} />)}
    <div style={{height: 190, borderRadius: 20, marginTop: 34, background: `linear-gradient(145deg,${accent}aa,#354d8c)`}} />
  </div>
);

const IllustrationBadge = () => <div style={{position: 'absolute', right: 64, top: 64, zIndex: 5, padding: '12px 18px', borderRadius: 24, color: '#fff', background: '#07192acc', fontSize: 22, fontWeight: 800, letterSpacing: '.08em'}}>ILUSTRASI</div>;

export const VisualScene: React.FC<{scene: RenderScene; index: number}> = ({scene, index}) => {
  const mediaFiles = scene.mediaFiles || (scene.mediaFile ? [{file: scene.mediaFile, durationInFrames: scene.durationInFrames}] : []);
  const useExternal = mediaFiles.length > 0 && (scene.visualMode === 'media' || scene.visualMode === 'hybrid');
  if (!useExternal) return <GeneratedArt scene={{...scene, illustrativeOnly: scene.illustrativeOnly || scene.visualMode !== 'generated'}} index={index} />;
  let cursor = 0;
  return <AbsoluteFill>
    {mediaFiles.map((media, mediaIndex) => {
      const from = cursor;
      cursor += media.durationInFrames;
      const source = staticFile(media.file);
      const isVideo = /\.(mp4|mov|webm)$/i.test(media.file);
      return <Sequence key={`${media.file}-${mediaIndex}`} from={from} durationInFrames={media.durationInFrames}>
        {isVideo ? <OffthreadVideo src={source} muted loop style={{width: '100%', height: '100%', objectFit: 'cover'}} /> : <Img src={source} style={{width: '100%', height: '100%', objectFit: 'cover'}} />}
      </Sequence>;
    })}
    <div style={{position: 'absolute', inset: 0, background: scene.visualMode === 'hybrid' ? 'linear-gradient(0deg,#061a2bea,transparent 72%),linear-gradient(90deg,#061a2b99,transparent)' : '#061a2b44'}} />
    {scene.visualMode === 'hybrid' ? <GeneratedArt scene={scene} index={index} /> : <AbsoluteFill style={{justifyContent: 'center', padding: 86}}><h1 style={{fontSize: 76, color: 'white', textShadow: '0 5px 30px #000', maxWidth: 820}}>{scene.heading}</h1></AbsoluteFill>}
    {scene.illustrativeOnly ? <IllustrationBadge /> : null}
  </AbsoluteFill>;
};
