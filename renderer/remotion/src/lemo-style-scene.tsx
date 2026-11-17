import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import type {RenderScene} from './types';

export type LemoStyleSlug = 'kino-montage' | 'dark-documentary' | 'cel-anime-80s' | 'midcentury-toon' | 'brick-toy' | 'swiss-motion' | 'iso-infographic' | 'pixel-rpg' | 'isometric-picture-book' | 'whiteboard-explainer-no-hands' | 'colored-pencil-science' | 'copperplate-engraving' | 'stained-glass' | 'urban-sketch-pen-wash' | 'silkscreen-travel-poster' | 'risograph-print' | 'ukiyo-e' | 'red-paper-cut' | 'woodcut-print';
export const LEMO_SCENE_STYLES: LemoStyleSlug[] = ['kino-montage','dark-documentary','cel-anime-80s','midcentury-toon','brick-toy','swiss-motion','iso-infographic','pixel-rpg','isometric-picture-book','whiteboard-explainer-no-hands','colored-pencil-science','copperplate-engraving','stained-glass','urban-sketch-pen-wash','silkscreen-travel-poster','risograph-print','ukiyo-e','red-paper-cut','woodcut-print'];
const themes: Record<LemoStyleSlug, {paper: string; ink: string; accent: string; secondary: string; tag: string}> = {
  'kino-montage': {paper: '#17151b', ink: '#f4e9d0', accent: '#e8a74d', secondary: '#a93c35', tag: 'CONSTRUCTIVIST MONTAGE'},
  'dark-documentary': {paper: '#111a19', ink: '#e6e1cf', accent: '#e0b35c', secondary: '#77aaa0', tag: 'RADAR SWEEP DOCUMENTARY'},
  'cel-anime-80s': {paper: '#f5ead8', ink: '#24233a', accent: '#e84762', secondary: '#39a8bc', tag: '80s CEL ANIME'},
  'midcentury-toon': {paper: '#f2e5c8', ink: '#263333', accent: '#d85c42', secondary: '#e5b94e', tag: 'MID-CENTURY CARTOON'},
  'brick-toy': {paper: '#17263c', ink: '#f5f0dd', accent: '#f1a53b', secondary: '#4f9da1', tag: 'BRICK TOY'},
  'swiss-motion': {paper: '#f3f1e8', ink: '#191919', accent: '#e33d36', secondary: '#3155bc', tag: 'SWISS MOTION GRAPHICS'},
  'iso-infographic': {paper: '#f2f0e7', ink: '#26323c', accent: '#d87448', secondary: '#4c9b91', tag: 'ISOMETRIC INFOGRAPHIC'},
  'pixel-rpg': {paper: '#141d34', ink: '#f5efd1', accent: '#f3bf4e', secondary: '#55c8a4', tag: '16-BIT PIXEL RPG'},
  'isometric-picture-book': {paper: '#e5ece4', ink: '#263f46', accent: '#d78757', secondary: '#74a898', tag: 'ISOMETRIC PEOPLE SCENE'},
  'whiteboard-explainer-no-hands': {paper: '#fcfcf7', ink: '#263342', accent: '#e25747', secondary: '#378b9d', tag: 'WHITEBOARD EXPLAINER · NO HANDS'},
  'colored-pencil-science': {paper: '#f6f0dc', ink: '#34483d', accent: '#e87545', secondary: '#69a783', tag: 'COLORED-PENCIL SCIENCE'},
  'copperplate-engraving': {paper: '#eee3c7', ink: '#332c27', accent: '#a65337', secondary: '#64736a', tag: 'COPPERPLATE ENGRAVING'},
  'stained-glass': {paper: '#182d3b', ink: '#f2e6c7', accent: '#e9573f', secondary: '#47a6a1', tag: 'STAINED GLASS'},
  'urban-sketch-pen-wash': {paper: '#f1eadb', ink: '#383a37', accent: '#bd674d', secondary: '#638c91', tag: 'URBAN SKETCH · PEN & WASH'},
  'silkscreen-travel-poster': {paper: '#f1e8d0', ink: '#243c47', accent: '#dc5742', secondary: '#377f88', tag: 'SILKSCREEN TRAVEL POSTER'},
  'risograph-print': {paper: '#f4eedb', ink: '#292c3a', accent: '#e84e65', secondary: '#287f9d', tag: 'RISOGRAPH PRINT'},
  'ukiyo-e': {paper: '#e6e2d0', ink: '#253c4c', accent: '#b94a36', secondary: '#56839a', tag: 'UKIYO-E'},
  'red-paper-cut': {paper: '#f5e8d1', ink: '#422922', accent: '#c92e24', secondary: '#e5ad46', tag: 'RED PAPER-CUT'},
  'woodcut-print': {paper: '#e8dfc8', ink: '#302e26', accent: '#a04430', secondary: '#587263', tag: 'WOODCUT PRINT'},
};

const line = (y: number, color: string, opacity: number, key: number) => <path key={key} d={`M-30 ${y} Q260 ${y - 38} 545 ${y + 5} T1110 ${y - 10}`} fill="none" stroke={color} strokeWidth={key % 3 === 0 ? 12 : 5} opacity={opacity} />;

/** Compact Remotion implementations paired to exact styles in the Lemo source catalog. */
export const LemoStyleScene: React.FC<{scene: RenderScene; index: number; style: LemoStyleSlug}> = ({scene, index, style}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = themes[style];
  const reveal = interpolate(frame, [0, fps * 1.5], [0, 1], {extrapolateRight: 'clamp'});
  const drift = Math.sin(frame / 38) * 12;
  const label = (scene.visualPrompt || scene.narration || '').split(/[,.!?]/)[0].trim().slice(0, 44);
  const lines = Array.from({length: 15}, (_, i) => line(680 + i * 69, t.secondary, .12 + (i % 4) * .035, i));

  let motif: React.ReactNode;
  switch (style) {
    case 'kino-montage':
      motif = <g transform={`translate(${drift} 0)`}>
        <path d="M40 1210 420 780 570 910 175 1330Z" fill={t.secondary} opacity=".85"/><path d="m410 1010 550-330-90 620-380 210Z" fill={t.accent} opacity=".8"/>
        <circle cx="820" cy="485" r="155" fill={t.accent} opacity=".7"/><path d="M0 1490H1080M0 1540H1080" stroke={t.ink} strokeWidth="5" opacity=".4"/>
        <text x="65" y="250" fill={t.accent} fontSize="34" fontFamily="monospace" letterSpacing="9">01 / MOVEMENT / {String(index + 1).padStart(2, '0')}</text>
      </g>; break;
    case 'dark-documentary':
      motif = <g>
        <path d="M0 500H1080M0 1050H1080M150 300V1600M930 300V1600" stroke={t.secondary} strokeWidth="2" opacity=".28"/>
        <circle cx="540" cy="1030" r="300" fill="none" stroke={t.secondary} strokeWidth="5" opacity=".8"/><circle cx="540" cy="1030" r="180" fill="none" stroke={t.secondary} strokeWidth="3" opacity=".55"/>
        <path d={`M540 1030 L${540 + Math.cos(frame / 25) * 290} ${1030 + Math.sin(frame / 25) * 290}`} stroke={t.accent} strokeWidth="12" opacity=".85"/>
        <circle cx="540" cy="1030" r="16" fill={t.accent}/><text x="92" y="580" fill={t.accent} fontFamily="monospace" fontSize="25">FIELD RECORD · {String(frame).padStart(4, '0')}</text>
      </g>; break;
    case 'cel-anime-80s':
      motif = <g transform={`translate(${drift} 0)`}>
        {Array.from({length: 13}, (_, i) => <path key={i} d={`M${-40 + i * 100} 540 L${80 + i * 90} ${720 + (i % 3) * 35}`} stroke={t.secondary} strokeWidth="9" opacity=".52"/>)}
        <circle cx="805" cy="710" r="175" fill={t.accent} opacity=".82"/><path d="M-20 1260 Q260 980 480 1200T1120 1100V1670H-20Z" fill={t.secondary}/>
        <path d="M190 1420q90-210 180 0m-176-75q86-95 172 0" fill="none" stroke={t.ink} strokeWidth="25" strokeLinecap="round"/>
      </g>; break;
    case 'midcentury-toon':
      motif = <g transform={`translate(${drift} 0)`}>
        <circle cx="820" cy="790" r="150" fill={t.secondary}/><path d="M-20 1160Q220 960 410 1130T820 1090T1110 1120V1600H-20Z" fill={t.accent}/>
        <path d="M385 1070q75-170 150 0v190H385zm15-112q17-17 34 0m48 0q17-17 34 0" fill={t.ink}/><circle cx="430" cy="1030" r="8" fill={t.paper}/><circle cx="500" cy="1030" r="8" fill={t.paper}/>
        <path d="M460 1125q-35 26-70 0m70 0q35 26 70 0" fill="none" stroke={t.paper} strokeWidth="12" strokeLinecap="round"/>
      </g>; break;
    case 'brick-toy':
      motif = <g transform={`translate(${drift} ${Math.sin(frame / 30) * 8})`} stroke={t.paper} strokeWidth="5" strokeLinejoin="round">
        <path d="m540 720 280 150-280 150-280-150Z" fill={t.accent}/><path d="M260 870v330l280 160v-340Z" fill={t.secondary}/><path d="M820 870v330l-280 160v-340Z" fill="#bd6547"/>
        {[0,1,2,3].map(i=><circle key={i} cx={380+i*105} cy={845+i%2*12} r="25" fill={t.paper} stroke="none" opacity=".8"/>)}
        <ellipse cx="540" cy="1400" rx="390" ry="90" fill="#07111f" stroke="none" opacity=".34"/>
      </g>; break;
    case 'swiss-motion':
      motif = <g>
        <path d="M80 470H1000M80 710H1000M80 950H1000M80 1190H1000M80 1430H1000" stroke={t.ink} strokeWidth="2" opacity=".24"/>
        <rect x={90 + (frame % 100) * 3} y="770" width="470" height="145" fill={t.accent}/><rect x="650" y="1000" width="300" height="300" fill={t.secondary}/>
        <circle cx="845" cy="600" r="130" fill={t.accent}/><text x="92" y="615" fill={t.ink} fontSize="42" fontWeight="800" letterSpacing="2">FORM / RHYTHM / TYPE</text>
      </g>; break;
    case 'iso-infographic':
      motif = <g>
        {[0,1,2,3,4].map(i=><g key={i} transform={`translate(${280+i*115} ${1020-i*30})`}><path d="m0 0 90-52 90 52-90 52Z" fill={i%2?t.secondary:t.accent}/><path d="M0 0v150l90 52V52Z" fill={t.secondary} opacity=".85"/><path d="M180 0v150l-90 52V52Z" fill={t.accent} opacity=".72"/><text x="85" y="-85" textAnchor="middle" fill={t.ink} fontSize="28">{(i+1)*12}%</text></g>)}
        <path d="M160 1450H930" stroke={t.ink} strokeWidth="6" opacity=".45"/>
      </g>; break;
    case 'pixel-rpg':
      motif = <g shapeRendering="crispEdges">
        <path d="M0 1390H1080V1920H0Z" fill="#263d54"/>{Array.from({length: 11},(_,i)=><rect key={i} x={i*110} y={1250+(i%3)*40} width="85" height="180" fill={i%2?t.secondary:t.accent} opacity=".84"/>)}
        <g transform={`translate(${450+Math.round(Math.sin(frame/18)*2)*24} 1060)`} fill={t.accent}><rect x="0" y="0" width="120" height="120"/><rect x="-24" y="120" width="168" height="144"/><rect x="0" y="264" width="48" height="120"/><rect x="96" y="264" width="48" height="120"/></g>
        {Array.from({length: 8},(_,i)=><rect key={`s${i}`} x={100+i*125} y={850+(i%2)*80} width="24" height="24" fill={t.paper}/>)}
      </g>; break;
    case 'isometric-picture-book':
      motif = <g transform={`translate(${drift} 0)`}>
        {Array.from({length: 5},(_,i)=><path key={i} d={`M${-300+i*290} 1250 L540 760 L${1380-i*290} 1250 L540 1740Z`} fill="none" stroke={t.secondary} strokeWidth="3" opacity=".55"/>)}
        <path d="m540 780 260 150-260 150-260-150Z" fill={t.paper} stroke={t.ink} strokeWidth="5"/><path d="M280 930v300l260 150v-300Z" fill={t.secondary} stroke={t.ink} strokeWidth="5"/><path d="M800 930v300l-260 150v-300Z" fill={t.accent} stroke={t.ink} strokeWidth="5"/>
        <circle cx="540" cy="980" r="42" fill={t.ink}/><path d="M540 1020v135m0-75-70 40m70-40 70 40" stroke={t.ink} strokeWidth="22" strokeLinecap="round"/>
      </g>; break;
    case 'whiteboard-explainer-no-hands':
      motif = <g><path d="M145 1230 C300 990 430 1220 540 980 S790 1000 930 770" fill="none" stroke={t.ink} strokeWidth="9" strokeLinecap="round" strokeDasharray="1100" strokeDashoffset={Math.max(0,1100-frame*16)}/>{[[175,1190],[485,1075],[745,1030],[920,790]].map(([x,y],i)=><g key={i} transform={`translate(${x} ${y+Math.sin(frame/12+i)*8})`}><circle r="61" fill={t.paper} stroke={i%2?t.secondary:t.accent} strokeWidth="12"/><path d={i%2?'M-24 0H24M0-24V24':'M-22 14 0-20 24 14Z'} fill="none" stroke={t.ink} strokeWidth="8" strokeLinejoin="round"/></g>)}<rect x="175" y="650" width="730" height="92" rx="12" fill={t.secondary} opacity=".16"/><path d={`M205 696H${Math.min(870,205+frame*8)}`} stroke={t.secondary} strokeWidth="16" strokeLinecap="round"/></g>; break;
    case 'colored-pencil-science':
      motif = <g transform={`translate(${Math.sin(frame/22)*16} 0)`}><circle cx="540" cy="1050" r="220" fill={t.secondary} opacity=".2" stroke={t.ink} strokeWidth="6"/><ellipse cx="540" cy="1050" rx="310" ry="115" fill="none" stroke={t.accent} strokeWidth="9" transform={`rotate(${-18+frame/5} 540 1050)`}/><circle cx="780" cy="965" r="32" fill={t.accent}/>{Array.from({length:9},(_,i)=><path key={i} d={`M${190+i*88} 1410 Q${160+i*85} ${1000-i%3*75} ${220+i*80} ${670+i%4*50}`} fill="none" stroke={i%2?t.secondary:t.accent} strokeWidth="7" opacity=".78"/>)}{Array.from({length:6},(_,i)=><path key={`h${i}`} d={`M${350+i*28} 780l${30+i*4} -45`} stroke={t.ink} strokeWidth="3" opacity=".4"/>)}</g>; break;
    case 'copperplate-engraving':
      motif = <g><g transform={`rotate(${Math.sin(frame/36)*3} 540 1040)`}><circle cx="540" cy="1040" r="300" fill="none" stroke={t.ink} strokeWidth="7"/><circle cx="540" cy="1040" r="245" fill="none" stroke={t.accent} strokeWidth="4" strokeDasharray="3 10"/>{Array.from({length:19},(_,i)=><path key={i} d={`M${280+i*28} 720l${-150+i*18} 630`} stroke={t.ink} strokeWidth="3" opacity=".45"/>)}<path d={"M540 1300V870m0 85-145-105m145 105 140-140M540 1060l-110 135m110-135 115 135"} fill="none" stroke={t.ink} strokeWidth="20" strokeLinecap="round"/><circle cx="540" cy="820" r="75" fill={t.secondary} stroke={t.ink} strokeWidth="8"/></g><text x="180" y="1460" fill={t.accent} fontFamily="Georgia" fontSize="28" letterSpacing="5">FIG. {String(index+1).padStart(2,'0')} · SCIENTIFIC PLATE</text></g>; break;
    case 'stained-glass':
      motif = <g>{Array.from({length:8},(_,i)=><g key={i} transform={`rotate(${Math.sin(frame/30+i)*4} 540 1050)`}><path d={`M540 1050 L${540+Math.cos(i*Math.PI/4)*430} ${1050+Math.sin(i*Math.PI/4)*610} Q540 ${1500+Math.sin(frame/20+i)*16} ${540+Math.cos((i+1)*Math.PI/4)*430} ${1050+Math.sin((i+1)*Math.PI/4)*610}Z`} fill={i%3===0?t.accent:i%2?t.secondary:'#e4b94f'} stroke={t.ink} strokeWidth="13" opacity=".88"/></g>)}<circle cx="540" cy="1050" r="130" fill={t.paper} stroke={t.ink} strokeWidth="14"/><circle cx="540" cy="1050" r={48+Math.sin(frame/12)*7} fill={t.accent}/></g>; break;
    case 'urban-sketch-pen-wash':
      motif = <g><path d="M80 1370Q170 1120 250 1370T430 1330T600 1380T780 1300T1010 1360V1530H80Z" fill={t.secondary} opacity=".28"/><path d="M130 1300V880h150v420m40 0V720h180v580m30 0V960h115v340m40 0V820h185v480" fill="none" stroke={t.ink} strokeWidth="9" strokeDasharray="1600" strokeDashoffset={Math.max(0,1600-frame*18)}/>{Array.from({length:13},(_,i)=><g key={i} transform={`translate(${175+(i%5)*45} ${940+Math.floor(i/5)*105})`}><rect width="18" height="36" fill={t.accent} opacity={.45+Math.sin(frame/12+i)*.12}/></g>)}<path d={`M690 ${820+Math.sin(frame/18)*18}q90-80 180 0`} fill="none" stroke={t.accent} strokeWidth="7"/></g>; break;
    case 'silkscreen-travel-poster':
      motif = <g><circle cx="780" cy={760+Math.sin(frame/24)*22} r="155" fill={t.accent}/><path d={`M-20 ${1370+Math.sin(frame/22)*18} 290 880 480 1140 705 760 1100 1380V1600H-20Z`} fill={t.secondary}/><path d="M-20 1450 390 1070 590 1300 820 990 1110 1480V1690H-20Z" fill={t.ink} opacity=".8"/>{[0,1,2,3].map(i=><rect key={i} x={95+i*235+((frame*2)%40)} y={1500+i%2*42} width="160" height="24" fill={t.paper} opacity=".82"/>)}<path d="M95 660H570" stroke={t.ink} strokeWidth="12" strokeDasharray="475" strokeDashoffset={Math.max(0,475-frame*9)}/></g>; break;
    case 'risograph-print':
      motif = <g>{[0,1,2,3,4].map(i=><g key={i} transform={`translate(${Math.sin(frame/17+i)*13} ${Math.cos(frame/15+i)*10}) rotate(${i*14-28} 540 1060)`}><circle cx={300+i*120} cy={900+i%2*180} r="210" fill={i%2?t.accent:t.secondary} opacity=".54"/><path d={`M${170+i*85} 1290h${230+i%3*22}v190h-${230+i%3*22}Z`} fill={i%2?t.secondary:t.accent} opacity=".58"/></g>)}<path d="M150 1470H930" stroke={t.ink} strokeWidth="8" strokeDasharray="12 16" strokeDashoffset={-frame*2}/></g>; break;
    case 'ukiyo-e':
      motif = <g><circle cx="790" cy="780" r="175" fill={t.accent} opacity=".84"/><path d={`M-40 ${1300+Math.sin(frame/14)*22} Q160 ${1110+Math.sin(frame/17)*20} 350 1290T740 1270T1120 1300V1580H-40Z`} fill={t.secondary} stroke={t.ink} strokeWidth="8"/><path d={`M-20 ${1400+Math.sin(frame/12)*24} Q200 1210 430 1390T850 1360T1100 1400`} fill="none" stroke={t.paper} strokeWidth="18"/>{Array.from({length:10},(_,i)=><path key={i} d={`M${80+i*100} 1270q-35 30 0 60`} fill="none" stroke={t.paper} strokeWidth="7"/>)}<path d="M500 900q50-160 100 0v210H500z" fill={t.paper} stroke={t.ink} strokeWidth="7"/><path d="M550 740v160" stroke={t.ink} strokeWidth="9"/></g>; break;
    case 'red-paper-cut':
      motif = <g>{Array.from({length:6},(_,i)=><g key={i} transform={`translate(${Math.sin(frame/20+i)*18} ${Math.cos(frame/18+i)*15})`}><path d={`M${110+i*150} 1420 Q${120+i*150} ${920-i%2*80} ${245+i*150} 1420Z`} fill={i%2?t.accent:'#df3d2e'} stroke={t.paper} strokeWidth="4"/><circle cx={180+i*150} cy={1050-i%2*80} r={40+i%3*10} fill={t.secondary} stroke={t.paper} strokeWidth="5"/><path d={`M${145+i*150} 1240l35-80 35 80`} fill="none" stroke={t.paper} strokeWidth="7"/></g>)}<path d="M120 1470H960" stroke={t.accent} strokeWidth="18" strokeDasharray="22 20" strokeDashoffset={-frame*3}/></g>; break;
    case 'woodcut-print':
      motif = <g><path d={`M-30 ${1370+Math.sin(frame/25)*20} 150 1030 330 1330 540 800 770 1300 940 980 1120 1400V1630H-30Z`} fill={t.secondary} stroke={t.ink} strokeWidth="8"/><circle cx="820" cy="750" r="145" fill={t.accent}/>{Array.from({length:22},(_,i)=><path key={i} d={`M${90+i*43} ${1450-(i%5)*35}l${100-(i%3)*26} -${300+(i%4)*45}`} stroke={t.ink} strokeWidth="5" opacity=".65"/>)}<path d={`M${180+((frame*3)%600)} 720v640`} stroke={t.paper} strokeWidth="10" strokeDasharray="18 18"/></g>; break;
  }

  return <AbsoluteFill style={{backgroundColor: t.paper, overflow: 'hidden'}}>
    <svg viewBox="0 0 1080 1920" width="100%" height="100%" preserveAspectRatio="xMidYMid slice">
      <rect width="1080" height="1920" fill={t.paper}/>
      {style === 'dark-documentary' || style === 'pixel-rpg' ? <>{Array.from({length: 18},(_,i)=><path key={i} d={`M0 ${i*112}H1080`} stroke={t.ink} strokeWidth="1" opacity=".055"/> )}</> : null}
      {lines}
      <g opacity={reveal}>{motif}</g>
      {style === 'kino-montage' ? <><rect x="0" y="0" width="1080" height="115" fill={t.paper}/><rect x="0" y="1805" width="1080" height="115" fill={t.paper}/></> : null}
      <text x="72" y="230" fill={t.ink} fontFamily={style === 'pixel-rpg' ? 'monospace' : 'Georgia, serif'} fontWeight="700" fontSize="62" opacity={reveal}>{scene.heading}</text>
      <text x="76" y="292" fill={t.accent} fontFamily="monospace" fontSize="21" letterSpacing="5" opacity={reveal}>{t.tag} · {String(index+1).padStart(2,'0')}</text>
      <path d="M76 326H1004" stroke={t.ink} strokeWidth="2" opacity=".26"/>
      <text x="78" y="1710" fill={t.ink} fontFamily="Georgia, serif" fontSize="30" opacity={reveal*.82}>{label}</text>
      <text x="78" y="1782" fill={t.secondary} fontFamily="monospace" fontSize="20" letterSpacing="4" opacity={reveal*.72}>{LEMO_SCENE_STYLES.indexOf(style) >= 9 ? 'OLYPHANTS ORIGINAL STYLE' : 'LEMO-OPUSCAR SOURCE STYLE'}</text>
      {style === 'kino-montage' ? <g fill={t.ink} opacity=".65">{Array.from({length:10},(_,i)=><rect key={i} x={25+i*105} y="35" width="54" height="45" rx="5"/>)}{Array.from({length:10},(_,i)=><rect key={i} x={25+i*105} y="1840" width="54" height="45" rx="5"/>)}</g> : null}
    </svg>
  </AbsoluteFill>;
};
