import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import type {RenderScene} from './types';

const palette = ['#b96745', '#829476', '#b9a267', '#637e87', '#455e4b'];
const hash = (value: number) => {
  const n = Math.sin(value * 127.1 + 311.7) * 43758.5453;
  return n - Math.floor(n);
};

/** Lemo-Opuscar Watercolor Brush visual treatment, composed by Remotion. */
export const LemoWatercolorScene: React.FC<{scene: RenderScene; index: number}> = ({scene, index}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const reveal = interpolate(frame, [0, fps * 3], [0, 1], {extrapolateRight: 'clamp'});
  const drift = Math.sin(frame / Math.max(1, fps * 2.8)) * 7;
  const sway = Math.sin(frame / Math.max(1, fps * 1.6)) * 4;
  const title = scene.heading || 'Catatan visual';
  const ink = '#342b25';
  const wash = palette[index % palette.length];
  const label = (scene.visualPrompt || scene.narration || '').split(/[,.!?]/)[0].trim().slice(0, 54);
  const branches = Array.from({length: 9}, (_, i) => {
    const x = 480 + i * 43;
    const length = 140 + hash(index * 91 + i) * 180;
    const sway = (hash(index * 101 + i) - .5) * 110;
    return {x, length, sway, opacity: .28 + hash(i + index * 7) * .22};
  });
  const flecks = Array.from({length: 70}, (_, i) => ({
    x: 32 + hash(index * 991 + i) * 1016,
    y: 36 + hash(index * 199 + i * 13) * 1848,
    r: .6 + hash(index * 17 + i * 3) * 1.8,
    opacity: .06 + hash(i * 11 + index) * .12,
  }));

  return <AbsoluteFill style={{backgroundColor: '#f1e9da', overflow: 'hidden'}}>
    <svg viewBox="0 0 1080 1920" width="100%" height="100%" preserveAspectRatio="xMidYMid slice" aria-label="Lemo Watercolor Brush">
      <defs>
        <linearGradient id={`sky-${index}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#d4d7c7" stopOpacity=".7" />
          <stop offset="1" stopColor="#f1e9da" stopOpacity="0" />
        </linearGradient>
      </defs>
      <rect width="1080" height="1920" fill={`url(#sky-${index})`} />
      <rect width="1080" height="1920" fill="#bba989" opacity=".045" />
      {flecks.map((f, i) => <circle key={`f-${i}`} cx={f.x} cy={f.y} r={f.r} fill={ink} opacity={f.opacity} />)}

      <g opacity={reveal} transform={`translate(${drift} 0)`}>
        <path d="M-80 1130 C130 970 230 1030 390 890 C570 735 710 850 850 700 C950 610 1060 640 1170 540 L1170 1510 L-80 1510Z" fill="#9eaa84" opacity=".35" />
        <path d="M-90 1285 C120 1090 260 1170 430 1035 C600 900 760 960 910 815 C1030 710 1100 760 1180 705 L1180 1600 L-90 1600Z" fill={wash} opacity=".23" />
        <path d="M-80 1455 C180 1250 290 1340 470 1200 C640 1070 810 1170 970 1000 C1060 920 1110 950 1180 900 L1180 1730 L-80 1730Z" fill="#7a8871" opacity=".25" />
        <path d="M-30 1470 Q260 1415 500 1485 T1110 1440" fill="none" stroke="#536b55" strokeWidth="14" strokeLinecap="round" opacity=".36" />
        <path d="M-20 1530 Q240 1495 510 1542 T1110 1510" fill="none" stroke="#c19b61" strokeWidth="26" strokeLinecap="round" opacity=".2" />
      </g>

      <g fill="none" strokeLinecap="round" strokeLinejoin="round" opacity={reveal} transform={`translate(${sway} 0)`}>
        <path d="M545 1455 C535 1280 550 1110 530 910 C520 805 540 700 575 610" stroke="#725540" strokeWidth="42" opacity=".18" />
        <path d="M545 1455 C535 1280 550 1110 530 910 C520 805 540 700 575 610" stroke="#71533e" strokeWidth="21" opacity=".66" strokeDasharray="260 9 310 7 500" strokeDashoffset={Math.max(0, 1200 - reveal * 1200)} />
        {branches.map((b, i) => <g key={`b-${i}`} opacity={b.opacity}>
          <path d={`M${530 + i * 2} ${925 - i * 20} Q${450 + b.sway} ${800 - i * 10} ${b.x + b.sway} ${925 - b.length}`} stroke="#526b4f" strokeWidth={10 + (i % 3) * 3} strokeDasharray="70 4 120 6 44" strokeDashoffset={Math.max(0, 310 - reveal * 310)} />
          <path d={`M${b.x + b.sway - 84} ${900 - b.length} Q${b.x + b.sway} ${790 - b.length} ${b.x + b.sway + 92} ${900 - b.length}`} stroke="#80966b" strokeWidth="18" opacity=".55" />
          <path d={`M${b.x + b.sway - 62} ${854 - b.length} Q${b.x + b.sway} ${825 - b.length} ${b.x + b.sway + 65} ${866 - b.length}`} stroke="#a5ad76" strokeWidth="8" opacity=".48" />
        </g>)}
        <path d="M180 1090 Q290 1015 395 1048" stroke="#596f59" strokeWidth="18" strokeDasharray="52 8 88 5 42" opacity=".44" />
        <path d="M690 1160 Q790 1080 920 1095" stroke="#647b63" strokeWidth="22" strokeDasharray="70 8 100 5 52" opacity=".4" />
      </g>

      <g fill={ink} opacity={reveal}>
        <circle cx="846" cy="390" r="75" fill="#c9784e" opacity=".32" />
        <circle cx="846" cy="390" r="58" fill="#c9784e" opacity=".22" />
      </g>
      <g fill={ink} textAnchor="middle">
        <text x="540" y="300" fontFamily="Georgia, serif" fontSize="55" fontStyle="italic" opacity={reveal}>{title}</text>
        <text x="540" y="365" fontFamily="Georgia, serif" fontSize="21" letterSpacing="6" opacity={reveal * .66}>WATERCOLOR BRUSH · LEMO-OPUSCAR STYLE</text>
      </g>
      <g fill="none" stroke={ink} strokeWidth="2" opacity={reveal * .56}>
        <path d="M730 600 Q780 570 815 590" />
        <path d="M804 590 l-18 -9 m18 9 l-16 12" />
      </g>
      <text x="830" y="584" fill={ink} fontFamily="cursive" fontSize="27" opacity={reveal * .74}>{label || 'catatan lapangan'}</text>
      <text x="540" y="1800" fill={ink} textAnchor="middle" fontFamily="Georgia, serif" fontStyle="italic" fontSize="25" opacity={reveal * .64}>Dibuat dengan teknik visual Watercolor Brush dari Lemo-Opuscar</text>
    </svg>
  </AbsoluteFill>;
};
